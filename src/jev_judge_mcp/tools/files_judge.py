"""jev_files_judge: one judgment over many files the server reads (ADR-0077).

The agent names paths — files, directories, or glob patterns — and writes one question; the server
expands them inside its working directory, prunes deterministically, and makes one provider call per
surviving file under the ADR-0069 in-flight cap. The payload reports a typed answer per judged file
and a stable reason per skipped one; no file's bytes ever enter the agent's context. This is the
extension-tool path ADR-0048 opened (divergence `file-judge-tools`): the question building and the
answer projection are `jev_file_judge`'s, the per-file read and its refusals are `file_state`'s, and
the usage summing is the `tools/files.py` `combined()` precedent — this module owns only the
expansion, the prune, and the bounded fan-out. Every prune step precedes the first provider call,
and a pruned path costs nothing.
"""

from collections.abc import Iterator, Sequence
from fnmatch import fnmatchcase
from pathlib import Path, PurePosixPath
from typing import Any, cast

import anyio

from jev_judge_mcp.domain import ChoiceQuestion, ScoreQuestion
from jev_judge_mcp.limits import FILE_JUDGE, FILES_JUDGE
from jev_judge_mcp.providers import Evaluation, ProviderConfigError
from jev_judge_mcp.tools.base import JevTool, Runtime, ToolError, ToolResult, define, frame
from jev_judge_mcp.tools.file_judge import (
    KIND_REFINEMENT,
    KINDS,
    ON_DEMAND_RULE,
    QUESTION_ID,
    build_question,
    project_answer,
)
from jev_judge_mcp.tools.file_state import read_state, resolve_scoped
from jev_judge_mcp.tools.files import combined
from jev_judge_mcp.tools.toolset import code_of

SKIP_DIRECTORIES = frozenset(
    {
        # VCS-internal
        ".git",
        ".hg",
        ".svn",
        # dependency installs
        "node_modules",
        ".venv",
        "venv",
        ".tox",
        "site-packages",
        # caches
        "__pycache__",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".cache",
        "coverage",
        # build output
        "build",
        "dist",
        "target",
    }
)
"""Directory names traversal never enters (ADR-0077): dependency installs, build output, caches,
and VCS internals are noise a judgment over source files never needs. A directly named *file*
inside one is still judged — the skip list governs traversal, not an explicit name."""

_GLOB_MAGIC = frozenset("*?[")
_FIRST_SHOWN = 3
"""How many paths an aggregate skip row names before the count takes over (ADR-0077)."""

_SKIP_REASONS = {
    "not_found": "not_found",
    "not_a_file": "not_found",
    "binary_file": "binary",
    "secret_file": "secret_file",
    "file_too_large": "too_large",
    "path_outside_scope": "outside_scope",
}
"""A per-file read refusal as a batch skip reason: the same verdicts, in the batch's vocabulary.
An unreadable file (a permissions race) lands in `not_found` too: the batch never judges what it
could not read, and the reason set stays the ADR's."""

DEFINITION = define(
    "jev_files_judge",
    "Judge many files you have not read",
    "Answer one question about many files without reading them: name paths — files, directories, or glob "
    "patterns — pick the question kind — noul (the probability a yes/no condition holds), choice (one option "
    "from `criteria`, an object of {option: description} entries), or score (a position on the ordered "
    "`criteria` levels, low to high) — and write `instructions` against each state's `content` field. The "
    "server expands the paths inside its working directory and prunes deterministically before any provider "
    "call: dependency, build, cache, and VCS-internal directories are not entered, known secret stores are "
    "never read, binary and empty files are skipped, each file is capped like jev_file_judge, and a hard cap "
    "of {FILES_JUDGE.files_max} surviving files bounds the call. One provider call runs per surviving file; a "
    "failed call is reported as a skipped path (call_failed:<code>), never a batch error, and the payload "
    "carries the typed answers plus the skipped paths with their reasons — summarized as one aggregate row "
    "per cause when a tree yields more than the caps — no file's bytes enter your context. Every read is "
    "credential-literal redacted before it is judged. To pick among the answers, run "
    "jev_find with them as candidates. Not for exact lookups, counting, math, or questions grep answers — "
    "run the command instead. "
    f"{ON_DEMAND_RULE}",
    {
        "type": "object",
        "properties": {
            "paths": {
                "type": "array",
                "items": {"type": "string", "minLength": 1},
                "minItems": 1,
                "maxItems": FILES_JUDGE.patterns_max,
                "description": f"Files, directories, or glob patterns (* ? [ with ** for levels), relative to "
                f"the server's working directory; resolved with symlinks followed, and a path that escapes it "
                f"is skipped. At most {FILES_JUDGE.patterns_max} entries; the same file named twice is judged once.",
            },
            "kind": {
                "type": "string",
                "description": f"The question type: one of {', '.join(KINDS)}.",
            },
            "instructions": {
                "type": "string",
                "minLength": 1,
                "maxLength": FILE_JUDGE.instructions_units_max,
                "description": f"The question, asked against each state's `content` field. Rejected above "
                f"{FILE_JUDGE.instructions_units_max:,} characters.",
            },
            "criteria": {
                "anyOf": [
                    {"type": "object"},
                    {
                        "type": "array",
                        "items": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": FILE_JUDGE.score_level_units_max,
                        },
                        "minItems": FILE_JUDGE.score_levels_min,
                        "maxItems": FILE_JUDGE.score_levels_max,
                    },
                ],
                "description": "Shaped by `kind`. noul: an object with optional `true` and `false` keys, each a "
                f"description of that outcome, at most {FILE_JUDGE.instructions_units_max:,} units. choice: an "
                f"object of {FILE_JUDGE.choice_options_min}-{FILE_JUDGE.choice_options_max} {{option: description}} "
                f"entries, each description at most {FILE_JUDGE.choice_option_units_max:,} units. score: the "
                f"ordered levels, {FILE_JUDGE.score_levels_min}-{FILE_JUDGE.score_levels_max} strings of "
                f"{FILE_JUDGE.score_level_units_max} units each, low to high.",
            },
            "recursive": {
                "type": "boolean",
                "description": "Walk named or matched directories to their leaves when true; the default reads "
                "each directory's immediate files only. The skip-list directories are never entered either way.",
            },
        },
        "required": ["paths", "kind", "instructions", "criteria"],
        "additionalProperties": False,
    },
)


def _has_glob_magic(entry: str) -> bool:
    return any(character in _GLOB_MAGIC for character in entry)


def _glob_pattern(entry: str) -> PurePosixPath | None:
    """The entry as a glob pattern, or `None` when it cannot stay inside the scope.

    `pathlib` raises on a non-relative pattern and silently walks out on `..` segments, so an
    escaping pattern is refused here, before any expansion, instead of being trusted to the
    per-match scope check.
    """
    pattern = PurePosixPath(entry)
    if pattern.is_absolute() or ".." in pattern.parts:
        return None
    return pattern


def _walk(directory: Path, *, recursive: bool) -> Iterator[Path]:
    """The files under `directory`, sorted per directory; skip-listed directories are never entered.

    A generator, so a caller with a full discovery budget never descends the rest of the tree.
    Non-recursive lists the immediate files only; recursive descends with the same in-place pruning
    a glob's directory expansion uses, so a skip-listed subtree is neither scanned nor listed.
    """
    if not recursive:
        for child in sorted(directory.iterdir()):
            if child.is_file():
                yield child
        return
    for root, directories, files in directory.walk(on_error=lambda _: None):
        directories[:] = sorted(name for name in directories if name not in SKIP_DIRECTORIES)
        for name in sorted(files):
            yield Path(root, name)


def _glob_files(root: Path, parts: tuple[str, ...]) -> Iterator[Path]:
    """The files a glob's last segment matches, expanded by a pruned walk (ADR-0077).

    `pathlib`'s `glob` descends into every directory the segment names — `node_modules`, `.git`,
    build output — and only filters afterwards, so a `**` over a real repo scans hundreds of
    thousands of entries before the prune runs. This expansion scans each level with `scandir`,
    matches segment patterns with `fnmatchcase` (the same POSIX case rules and dotfile behavior as
    `pathlib`), and never enters a skip-listed directory, so the prune happens during the walk.
    A `**` segment matches any depth including zero; a trailing `**` is every file under the
    frontier, per the `recursive` walk the caller asked for. Directories and files are yielded in
    sorted order per directory.
    """

    def scan(directory: Path, segment: str) -> Iterator[Path]:
        try:
            entries = sorted(directory.iterdir(), key=lambda child: child.name)
        except OSError:
            return
        for child in entries:
            if child.name in SKIP_DIRECTORIES:
                continue
            if fnmatchcase(child.name, segment):
                yield child

    *heads, tail = parts
    frontier: list[Path] = [root]
    for segment in heads:
        next_frontier: list[Path] = []
        for directory in frontier:
            if segment == "**":
                # Zero or more directories: the directory itself, then every kept descendant.
                next_frontier.append(directory)
                for root_, directories, _files in directory.walk(on_error=lambda _: None):
                    directories[:] = sorted(name for name in directories if name not in SKIP_DIRECTORIES)
                    next_frontier.extend(Path(root_, name) for name in sorted(directories))
            else:
                next_frontier.extend(child for child in scan(directory, segment) if child.is_dir())
        frontier = next_frontier
    for directory in frontier:
        if tail == "**":
            yield from _walk(directory, recursive=True)
        else:
            # Files and directories: a matched directory is walked per the caller's recursive
            # flag, exactly like a directory the caller named directly.
            yield from scan(directory, tail)


def _aggregate(reason: str, count: int, first: Sequence[str]) -> dict[str, str]:
    """One skip row summarizing `count` paths the payload does not list (ADR-0077).

    A tree beyond the caps would otherwise carry thousands of skip rows into the agent's context —
    the exact cost the tool exists to avoid — so the overflow collapses into one row per cause,
    naming the count and the first few paths. The reason vocabulary is unchanged.
    """
    shown = first[:_FIRST_SHOWN]
    names = ", ".join(shown) + (", …" if count > len(shown) else "")
    if reason == "over_the_file_cap":
        text = f"+{count} more files over the {FILES_JUDGE.files_max}-file cap"
    else:
        text = f"+{count} more {reason}"
    return {"path": f"{text}: {names}", "reason": reason}


def plan(
    paths: Sequence[str], scope: Path | None = None, *, recursive: bool = False
) -> tuple[list[tuple[str, str]], list[dict[str, str]]]:
    """Expand, prune, and read — the whole deterministic half of the tool, before any provider call.

    Returns the survivors as `(path text, redacted content)` in input-discovery order and every
    skipped path with its stable reason. `scope` is the server's working directory (the production
    default when `None`); tests pass a tmp tree. The check order is the ADR's: scope, the skip list
    on discovered paths, existence, the secret-store name, the binary sniff and the size cap inside
    the read, emptiness, then the hard surviving-files cap. The path text is the caller's entry for
    a directly named file — the same echo `jev_file_judge` makes — and the scope-relative posix
    path for everything discovered.

    Three bounds keep a real tree inside one call: discovery stops at `FILES_JUDGE.discovery_max`
    candidates (glob expansion never enters a skip-listed directory on the way), reading stops as
    soon as `FILES_JUDGE.files_max` survivors exist, and at most `FILES_JUDGE.skip_rows_max` skip
    rows are listed individually before the overflow collapses into one aggregate row per reason —
    count plus the first few paths. Nothing past a bound is read: an unexpanded or unread candidate
    is part of an aggregate, never a judgment.
    """
    root = (scope if scope is not None else Path.cwd()).resolve()
    skipped: list[dict[str, str]] = []
    overflow: dict[str, int] = {}
    overflow_first: dict[str, list[str]] = {}

    def skip(path: str, reason: str) -> None:
        """One skip row while the payload has room; past `skip_rows_max`, an aggregate instead."""
        if len(skipped) < FILES_JUDGE.skip_rows_max:
            skipped.append({"path": path, "reason": reason})
            return
        overflow[reason] = overflow.get(reason, 0) + 1
        first = overflow_first.setdefault(reason, [])
        if len(first) < _FIRST_SHOWN:
            first.append(path)

    candidates: list[tuple[Path, str]] = []
    seen: set[Path] = set()
    discovery_overflow = 0
    discovery_first: list[str] = []

    def discover(path: Path, text: str, *, traversed: bool) -> bool:
        """One discovered file, deduplicated by its resolved name; False when discovery is full.

        A traversed file (a directory walk or a glob match) faces the skip list and the scope check;
        a directly named file was already scoped by `resolve_scoped` and bypasses the skip list —
        the skip list governs traversal, not an explicit name. `text` is the caller's entry for a
        directly named file and the scope-relative posix path for everything discovered, so the
        payload always names a file the way the caller could have.
        """
        if len(candidates) >= FILES_JUDGE.discovery_max:
            nonlocal discovery_overflow
            discovery_overflow += 1
            if len(discovery_first) < _FIRST_SHOWN:
                discovery_first.append(text)
            return False
        resolved = path.resolve()
        if resolved in seen:
            return True
        if not resolved.is_relative_to(root):
            skip(path.relative_to(root).as_posix(), "outside_scope")
            return True
        if traversed and any(part in SKIP_DIRECTORIES for part in resolved.relative_to(root).parts):
            return True
        seen.add(resolved)
        candidates.append((resolved, text))
        return True

    for entry in paths:
        if _has_glob_magic(entry):
            pattern = _glob_pattern(entry)
            if pattern is None:
                skip(entry, "outside_scope")
                continue
            matched_any = False
            full = False
            for match in _glob_files(root, pattern.parts):
                matched_any = True
                if match.is_dir():
                    for discovered in _walk(match, recursive=recursive):
                        if not discover(discovered, discovered.relative_to(root).as_posix(), traversed=True):
                            full = True
                            break
                elif match.is_file():
                    if not discover(match, match.relative_to(root).as_posix(), traversed=True):
                        full = True
                if full:
                    break
            if not matched_any:
                skip(entry, "not_found")
            continue
        try:
            resolved = resolve_scoped(entry, scope=root)
        except (ToolError, ValueError):
            skip(entry, "outside_scope")
            continue
        if not resolved.exists():
            skip(entry, "not_found")
        elif resolved.is_dir():
            if resolved.name in SKIP_DIRECTORIES:
                skip(entry, "skipped_directory")
                continue
            walked_any = False
            for discovered in _walk(resolved, recursive=recursive):
                walked_any = True
                if not discover(discovered, discovered.relative_to(root).as_posix(), traversed=True):
                    break
            if not walked_any:
                skip(entry, "not_found")  # the directory exists but holds nothing to judge
        else:
            discover(resolved, entry, traversed=False)

    survivors: list[tuple[str, str]] = []
    cap_overflow = 0
    cap_first: list[str] = []
    for resolved, text in candidates:
        if len(survivors) == FILES_JUDGE.files_max:
            # Reading stops here: everything past the cap is one aggregate row, not a row per file.
            cap_overflow += 1
            if len(cap_first) < _FIRST_SHOWN:
                cap_first.append(text)
            continue
        try:
            content = read_state(resolved, caps=FILES_JUDGE)
        except ToolError as error:
            skip(text, _SKIP_REASONS[error.code])
            continue
        if not content:
            skip(text, "empty")
            continue
        survivors.append((text, content))
    if cap_overflow:
        skipped.append(_aggregate("over_the_file_cap", cap_overflow, cap_first))
    if discovery_overflow:
        # Discovery stopped mid-tree: the walk was aborted at the bound, so the remainder cannot be
        # counted without the scan the bound exists to avoid. The row names the bound and the first
        # unexpanded paths instead of a count.
        names = ", ".join(discovery_first) + ", …"
        skipped.append(
            {
                "path": f"discovery stopped at the {FILES_JUDGE.discovery_max}-candidate bound; not expanded: {names}",
                "reason": "over_the_file_cap",
            }
        )
    for reason, count in overflow.items():
        skipped.append(_aggregate(reason, count, overflow_first[reason]))
    return survivors, skipped


async def handle(args: dict[str, Any], runtime: Runtime) -> ToolResult:
    kind: str = args["kind"]
    question = build_question(kind, args["instructions"], args["criteria"])
    survivors, skipped = plan(args["paths"], recursive=bool(args.get("recursive", False)))

    evaluations: list[Evaluation | None] = [None] * len(survivors)
    failures: list[tuple[int, dict[str, str]]] = []
    config_error: list[ProviderConfigError] = []

    async def judge(index: int) -> None:
        path_text, content = survivors[index]
        try:
            evaluations[index] = await runtime.ask({"path": path_text, "content": content}, {QUESTION_ID: question})
        except ProviderConfigError as error:
            # The server cannot ask at all — not a per-file failure. Cancel the siblings and re-raise
            # outside the group, so the kernel's `auth` refusal keeps its code (the raw group would
            # flatten it to `provider`).
            config_error.append(error)
            task_group.cancel_scope.cancel()
        except Exception as error:  # a per-file failure is a skip, never a batch error (ADR-0077)
            failures.append((index, {"path": path_text, "reason": f"call_failed:{code_of(error)}"}))

    # One call per surviving file; the ADR-0069 in-flight cap in `runtime.ask` bounds the overlap
    # (the semaphore and the provider resolution it fronts are built synchronously, so concurrent
    # first calls cannot double-construct it).
    async with anyio.create_task_group() as task_group:
        for index in range(len(survivors)):
            task_group.start_soon(judge, index)
    if config_error:
        raise config_error[0]

    expected: Sequence[str] = ()
    if isinstance(question, ChoiceQuestion):
        expected = list(question.criteria)
    elif isinstance(question, ScoreQuestion):
        expected = cast("Sequence[str]", question.criteria)
    results: list[dict[str, object]] = []
    for index, (path_text, _) in enumerate(survivors):
        evaluation = evaluations[index]
        if evaluation is None:
            continue  # its call failed; the skip record below carries the reason
        answer, status = project_answer(kind, evaluation.answers.get(QUESTION_ID), expected)
        results.append({"path": path_text, "answer": answer, "status": status})
    asked = [evaluation for evaluation in evaluations if evaluation is not None]
    body: dict[str, object] = {
        "results": results,
        "skipped": skipped + [record for _, record in sorted(failures)],
        "calls": len(survivors),
    }
    return ToolResult(frame("jev_files_judge", combined(asked) if asked else None, body))


TOOL = JevTool(DEFINITION, handle, {"kind": KIND_REFINEMENT})
