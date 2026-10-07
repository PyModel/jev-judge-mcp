"""Reading a git range as a `[{path, patch}]` file list: the one boundary the gate CLI and
`jev_gate_range` share (ADR-0080).

Every git call is a fixed argv with `git -C <root>`, no shell, and a timeout. The range is the
caller's text and is checked before git sees it; git's output is untrusted text, and a header this
module cannot read keeps the whole diff a string rather than guessing a path. Failures raise
`GitDiffError`, whose message each caller wraps in its own refusal shape.
"""

import subprocess
import threading
from pathlib import Path

from jev_judge_mcp.limits import GATE

GIT_TIMEOUT_SECONDS = 30
"""Each git call's bound, so a hung git (a lock, a slow filesystem) cannot hold a gate open
indefinitely. A process bound shared by the CLI and the tool, not an input cap, so it is owned
here, not in `limits.py` (ADR-0080)."""


_CANONICAL = (
    "--no-color",
    "--no-ext-diff",
    "--no-textconv",
    "--no-renames",
    "--src-prefix=a/",
    "--dst-prefix=b/",
)
"""Flags that pin `git diff` to plain unified output whatever the operator's config sets: forced
color would put escape codes in the patch, `diff.noprefix` would defeat the `a/`/`b/` header split,
an external diff driver or textconv filter would replace the patch or run a program, and rename
detection would show a renamed file's old lines under its new name, past a guard keyed on names."""


class GitDiffError(Exception):
    """A range that could not be read. The message is the whole refusal text."""


class GitDiffTooLarge(GitDiffError):
    """A range whose diff passed the gate's aggregate budget while it was being read."""


def repo_root(cwd: Path) -> Path:
    """The resolved top level of the git work tree containing `cwd`."""
    run = _git(cwd, "rev-parse", "--show-toplevel")
    if run is None or run.returncode != 0 or not run.stdout.strip():
        raise GitDiffError("gate refuses to run outside a git repo")
    return Path(run.stdout.strip()).resolve()


def git_diff(repo: Path, rev_range: str, *, paths: tuple[str, ...] = ()) -> str:
    """`git diff <rev_range> [-- paths]` run in `repo`, read no further than the gate's budget.

    Refuses an empty range, a leading `-`, a NUL, and an end that does not name a commit or a tree:
    a blob diff's header carries an object id instead of a file name, so no per-path guard could
    see what it holds. `paths` are pathspecs resolved against `repo`, so `.` keeps the diff inside
    that directory.
    """
    if not rev_range or rev_range.startswith("-") or "\x00" in rev_range:
        raise GitDiffError("refusing that git range")
    for end in _range_ends(rev_range):
        if not _names_a_tree(repo, end):
            raise GitDiffError("refusing that git range")
    out = _bounded_diff(repo, [*_CANONICAL, rev_range, "--", *paths] if paths else [*_CANONICAL, rev_range])
    if not out.strip():
        raise GitDiffError("git diff was empty")
    return out


def diff_argument(repo: Path, rev_range: str, *, paths: tuple[str, ...] = ()) -> str | list[dict[str, str]]:
    """The range as jev_gate's `diff`: the file list, or the raw text when a header does not split."""
    diff = git_diff(repo, rev_range, paths=paths)
    files = split_unified(diff)
    return files if files is not None else diff


def _names_a_tree(repo: Path, end: str) -> bool:
    """Whether one end resolves to an object that peels to a tree: a commit, a tag of one, or a
    tree, including `rev:path`. Resolved to its id first, because `^{tree}` after `rev:path` would
    be read as part of the path."""
    resolved = _git(repo, "rev-parse", "--verify", "--quiet", end)
    if resolved is None or resolved.returncode != 0 or not resolved.stdout.strip():
        return False
    peeled = _git(repo, "rev-parse", "--verify", "--quiet", f"{resolved.stdout.strip()}^{{tree}}")
    return peeled is not None and peeled.returncode == 0


def _range_ends(rev_range: str) -> list[str]:
    """The named ends of `A..B`, `A...B`, or one revision; an omitted end is git's HEAD."""
    for separator in ("...", ".."):
        if separator in rev_range:
            return [end for end in rev_range.split(separator, 1) if end]
    return [rev_range]


def _bounded_diff(repo: Path, args: list[str]) -> str:
    """git's stdout, killed once it passes the byte bound or the timeout.

    Worst-case UTF-16 encoding is three UTF-8 bytes per unit, so crossing the byte bound proves the
    text is over the gate's aggregate budget: the kill is never a false refusal.
    """
    byte_cap = 3 * GATE.aggregate_evidence_units + 3
    try:
        process = subprocess.Popen(  # noqa: S603 - fixed argv0; the range was checked above
            ["git", "-C", str(repo), "diff", *args],  # noqa: S607
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
    except OSError as error:
        raise GitDiffError("git diff failed") from error
    # The context closes the pipe and reaps git on every path, the over-budget kill included.
    with process:
        timer = threading.Timer(GIT_TIMEOUT_SECONDS, process.kill)
        timer.start()
        try:
            assert process.stdout is not None  # stdout=PIPE
            data = process.stdout.read(byte_cap + 1)
            if len(data) > byte_cap:
                process.kill()
                raise GitDiffTooLarge(
                    f"the range's diff is over the {GATE.aggregate_evidence_units:,}-unit gate budget; "
                    "narrow the range or gate it in parts"
                )
            returncode = process.wait()
        finally:
            timer.cancel()
    if returncode != 0:
        raise GitDiffError("git diff failed")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise GitDiffError("git diff failed") from error


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str] | None:
    """One git call, or `None` when git could not run or did not finish in time."""
    try:
        return subprocess.run(  # noqa: S603 - fixed argv0; every caller checks its arguments first
            ["git", "-C", str(cwd), *args],  # noqa: S607
            check=False,
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired, UnicodeDecodeError):
        return None


def split_unified(diff: str) -> list[dict[str, str]] | None:
    if "diff --git " not in diff:
        return None
    parts = diff.split("\ndiff --git ")
    files: list[dict[str, str]] = []
    for index, part in enumerate(parts):
        chunk = part if index == 0 else "diff --git " + part
        if not chunk.strip():
            continue
        path = _path_from_header(chunk)
        if path is None:
            return None
        files.append({"path": path, "patch": chunk if chunk.endswith("\n") else chunk + "\n"})
    return files or None


def _path_from_header(chunk: str) -> str | None:
    """The post-image path of one `diff --git a/X b/X` header.

    Both halves name the same path unless the file was renamed, so the header is split at its
    midpoint rather than at the last ` b/`, which a path containing ` b/` would defeat. A rename
    (`a/old b/new`) has no such symmetry: its `+++ b/` line names the new path when the content
    changed (git ends that line with a tab when the path holds spaces), else the last ` b/` stands.
    """
    lines = chunk.splitlines()
    first = lines[0]
    rest = first.removeprefix("diff --git ")
    path: str | None = None
    middle = len(rest) // 2
    if len(rest) % 2 == 1 and rest[middle] == " ":
        left, right = rest[:middle], rest[middle + 1 :]
        if left.startswith("a/") and right.startswith("b/") and left[2:] == right[2:]:
            path = right[2:]
    if path is None:
        plus = next((line for line in lines[1:] if line.startswith("+++ b/")), None)
        if plus is not None:
            path = plus.removeprefix("+++ b/").rstrip("\t")
    if path is None:
        marker = " b/"
        if marker not in first:
            return None
        path = first.rsplit(marker, 1)[-1]
    if not path or path.startswith("/") or ".." in Path(path).parts:
        return None
    return path
