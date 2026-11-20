"""Reading a git range as a `[{path, patch}]` file list: the one boundary the gate CLI and
`jev_gate_range` share (ADR-0080).

Every git call is a fixed argv with `git -C <root>`, no shell, and a timeout. The range is the
caller's text and is checked before git sees it; git's output is untrusted text, and a header this
module cannot read keeps the whole diff a string rather than guessing a path. Failures raise
`GitDiffError`, whose message each caller wraps in its own refusal shape.
"""

import codecs
import os
import re
import subprocess
from collections.abc import Mapping
from pathlib import Path

from jev_judge_mcp.limits import GATE
from jev_judge_mcp.process import OutputTooLarge, capture

GIT_TIMEOUT_SECONDS = 30
"""Each git call's bound, so a hung git (a lock, a slow filesystem) cannot hold a gate open
indefinitely. A process bound shared by the CLI and the tool, not an input cap, so it is owned
here, not in `limits.py` (ADR-0080)."""


_CANONICAL = (
    "--no-color",
    "--no-relative",
    "--submodule=short",
    "--no-ext-diff",
    "--no-textconv",
    "--no-renames",
    "--unified=3",
    "--inter-hunk-context=0",
    "--src-prefix=a/",
    "--dst-prefix=b/",
)
"""Flags that pin the patch format and normal hunk context independently of the operator's config: forced
color would put escape codes in the patch, `diff.noprefix` would defeat the `a/`/`b/` header split,
an external diff driver or textconv filter would replace the patch or run a program, and rename
detection would show a renamed file's old lines under its new name, past a guard keyed on names."""


class GitDiffError(Exception):
    """A range that could not be read. The message is the whole refusal text."""


class GitDiffTooLarge(GitDiffError):
    """A range whose diff passed the gate's aggregate budget while it was being read."""


_CAPTURE_BYTES = 3 * GATE.aggregate_evidence_units + 3


def run(
    cwd: Path,
    *args: str,
    binary: str = "git",
    env: Mapping[str, str] | None = None,
    byte_cap: int = _CAPTURE_BYTES,
    ignore_user_config: bool = False,
) -> subprocess.CompletedProcess[bytes]:
    """Run fixed Git policy in cwd; only explicit caller options can select a different Git directory."""
    source = os.environ if env is None else env
    clean = {name: value for name, value in source.items() if not name.startswith("GIT_")}
    clean.update(GIT_NO_LAZY_FETCH="1", GIT_ALLOW_PROTOCOL="")
    if ignore_user_config:
        clean.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull)
    return capture(
        [
            binary,
            "-C",
            str(cwd),
            "--no-pager",
            "--no-optional-locks",
            "--no-replace-objects",
            "-c",
            "core.fsmonitor=false",
            "-c",
            f"core.hooksPath={os.devnull}",
            *args,
        ],
        cwd=cwd,
        env=clean,
        timeout_seconds=GIT_TIMEOUT_SECONDS,
        byte_cap=byte_cap,
    )


def repo_root(cwd: Path) -> Path:
    """The resolved top level of the git work tree containing `cwd`."""
    result = _git(cwd, "rev-parse", "--show-toplevel")
    if result is None or result.returncode or not result.stdout.strip():
        raise GitDiffError("gate refuses to run outside a git repo")
    return Path(result.stdout.rstrip("\n")).resolve()


def scoped_range(repo: Path, rev_range: str) -> tuple[str, set[Path]]:
    """Pin both endpoints and bind the path printed by Git to each possible repository origin."""
    if not rev_range or rev_range.startswith("-") or "\x00" in rev_range:
        raise GitDiffError("refusing that git range")
    sep = "..." if "..." in rev_range else ".." if ".." in rev_range else ""
    ends = rev_range.split(sep, 1) if sep else [rev_range]
    origins = set[Path]() if sep else {Path(".")}  # a single revision compares to the working tree
    ids: list[str] = []
    for end in ends:
        rev, colon, subtree = (end or "HEAD").partition(":")
        if (
            not rev
            or rev.startswith("-")
            or (colon and (sep == "..." or any(part in {".", "..", ""} for part in subtree.split("/") if subtree)))
        ):
            raise GitDiffError("refusing that git range")
        commit = _git(
            repo, "rev-parse", "--verify", "--quiet", "--end-of-options", rev.removesuffix("^{tree}") + "^{commit}"
        )
        if commit is not None and commit.returncode == 0:
            oid = commit.stdout.strip()
            if colon:
                tree = _git(repo, "rev-parse", "--verify", "--quiet", "--end-of-options", f"{oid}:{subtree}")
                if tree is None or tree.returncode:
                    raise GitDiffError("refusing that git range")
                picked = _git(repo, "rev-parse", "--verify", "--quiet", f"{tree.stdout.strip()}^{{tree}}")
                if picked is None or picked.returncode:
                    raise GitDiffError("refusing that git range")
                oid = picked.stdout.strip()
            ids.append(oid)
            origins.add(Path(subtree))
        else:
            # A bare nonempty tree could be a subtree at any prefix. Only an empty tree
            # contributes no paths and can therefore be accepted without origin information.
            empty = _git(repo, "hash-object", "-t", "tree", "--stdin")
            if colon or sep == "..." or empty is None or empty.returncode or rev != empty.stdout.strip():
                raise GitDiffError("refusing that git range")
            ids.append(rev)
    return sep.join(ids), origins


def diff_argument(
    repo: Path, rev_range: str, *, paths: tuple[str, ...], flags: tuple[str, ...]
) -> str | list[dict[str, str]]:
    """Read a range pinned by scoped_range and split the canonical unified output."""
    try:
        result = run(repo, "diff", *_CANONICAL, *flags, rev_range, "--", *paths)
        if result.returncode:
            raise GitDiffError("git diff failed")
        diff = result.stdout.decode("utf-8")
    except OutputTooLarge as error:
        raise GitDiffTooLarge(
            f"the range's diff exceeds the {_CAPTURE_BYTES:,}-byte Git capture limit; narrow the range"
        ) from error
    except (OSError, subprocess.TimeoutExpired, UnicodeDecodeError) as error:
        raise GitDiffError("git diff failed") from error
    if not diff.strip():
        return []
    files = split_unified(diff)
    return files if files is not None else diff


def range_has_changes(cwd: Path, rev_range: str) -> bool | None:
    """Use the patch reader's diff policy; None means that Git could not compare the revisions."""
    try:
        result = run(cwd, "diff", *_CANONICAL, "--quiet", rev_range, "--")
    except (OutputTooLarge, OSError, subprocess.TimeoutExpired) as error:
        raise GitDiffError("git diff failed") from error
    if result.returncode in (0, 1):
        return result.returncode == 1
    return None


def untracked_paths(cwd: Path) -> list[str]:
    try:
        result = run(cwd, "ls-files", "--others", "--exclude-standard", "-z", "--", ".")
        if result.returncode:
            raise GitDiffError("git could not list untracked files")
        return result.stdout.decode("utf-8").split("\0")[:-1]
    except OutputTooLarge as error:
        raise GitDiffTooLarge("the range exceeds the Git capture limit for untracked paths") from error
    except (OSError, subprocess.TimeoutExpired, UnicodeDecodeError) as error:
        raise GitDiffError("git could not list untracked files") from error


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str] | None:
    try:
        result = run(cwd, *args)
        return subprocess.CompletedProcess(result.args, result.returncode, result.stdout.decode("utf-8"))
    except (OSError, subprocess.TimeoutExpired, UnicodeDecodeError, OutputTooLarge):
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
    """Decode the symmetric a/ and b/ names emitted with renames disabled."""
    first = chunk.partition("\n")[0]
    rest = first.removeprefix("diff --git ")
    path: str | None = None
    if rest.startswith('"'):
        # Git uses C quoting for arbitrary filename bytes; accept only its canonical escapes.
        field = r'"((?:[^"\\]|\\(?:[abtnvfr"\\]|[0-3][0-7]{2}))*)"'
        pair = re.fullmatch(field + " " + field, rest)
        if pair is None:
            return None
        try:
            left, right = (codecs.escape_decode(value.encode())[0].decode("utf-8") for value in pair.groups())
        except (UnicodeError, ValueError):
            return None
        if not left.startswith("a/") or right != "b/" + left[2:]:
            return None
        path = right[2:]
    middle = len(rest) // 2
    if len(rest) % 2 == 1 and rest[middle] == " ":
        left, right = rest[:middle], rest[middle + 1 :]
        if left.startswith("a/") and right.startswith("b/") and left[2:] == right[2:]:
            path = right[2:]
    if not path or "\x00" in path or path.startswith("/") or ".." in Path(path).parts:
        return None
    return path
