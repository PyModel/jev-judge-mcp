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

import logging
import os
import time
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field
from fnmatch import fnmatchcase
from pathlib import Path, PurePosixPath
from typing import Any, cast

import anyio

from jev_judge_mcp.domain import ChoiceQuestion, ScoreQuestion
from jev_judge_mcp.limits import FILE_JUDGE, FILES_JUDGE
from jev_judge_mcp.providers import Evaluation, ProviderConfigError
from jev_judge_mcp.security import SecurityGuard
from jev_judge_mcp.tools.base import JevTool, Runtime, ToolError, ToolResult, code_of, define, frame
from jev_judge_mcp.tools.file_judge import (
    KIND_REFINEMENT,
    KINDS,
    ON_DEMAND_RULE,
    QUESTION_ID,
    build_question,
    project_answer,
)
from jev_judge_mcp.tools.file_state import pinned_directory, read_state, resolve_scoped
from jev_judge_mcp.tools.files import combined

logger = logging.getLogger(__name__)

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
    f"of {FILES_JUDGE.files_max} surviving files bounds the call. One provider call runs per surviving file; a "
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


@dataclass
class _Discovery:
    remaining: int
    unreadable: Callable[[Path], None]
    check_cancelled: Callable[[], None]
    transitions_left: int
    failures: int = 0
    stopped: Path | None = None
    listings: dict[Path, list[Path]] = field(default_factory=dict[Path, list[Path]])

    def children(self, directory: Path) -> list[Path]:
        """Only sort a completed listing. A partial directory has no deterministic prefix."""
        self.check_cancelled()
        if self.stopped is not None:
            return []
        if directory in self.listings:
            return self.listings[directory]
        if self.remaining == 0:
            self.stopped = directory
            return []
        self.remaining -= 1
        entries: list[Path] = []
        try:
            with pinned_directory(directory) as fd, os.scandir(fd) as scan:
                for entry in scan:
                    self.check_cancelled()
                    if self.remaining == 0:
                        self.stopped = directory
                        return []
                    self.remaining -= 1
                    if entry.name not in SKIP_DIRECTORIES:
                        entries.append(directory / entry.name)
        except OSError:
            self.failures += 1
            self.unreadable(directory)
            return []
        entries.sort()
        self.listings[directory] = entries
        return entries


def _walk(directory: Path, budget: _Discovery, *, recursive: bool) -> Iterator[Path]:
    pending = [directory]
    while pending and budget.stopped is None:
        children = budget.children(pending.pop())
        if recursive:
            pending.extend(child for child in reversed(children) if not child.is_symlink() and child.is_dir())
        yield from (child for child in children if child.is_file())


def _glob_files(
    root: Path, parts: tuple[str, ...], budget: _Discovery, outside: Callable[[Path], None]
) -> Iterator[Path]:
    pending = [(root, root, 0)]
    visited: set[tuple[Path, int]] = set()
    while pending and budget.stopped is None:
        directory, display, index = pending.pop()
        if (directory, index) in visited:
            continue
        if budget.transitions_left == 0:
            budget.stopped = directory
            break
        budget.transitions_left -= 1
        visited.add((directory, index))
        segment = parts[index]
        last = index + 1 == len(parts)
        if segment == "**" and last:
            for child in _walk(directory, budget, recursive=True):
                yield display / child.relative_to(directory)
            continue
        children = budget.children(directory)
        if segment == "**":
            pending.extend(
                (child, display / child.name, index)
                for child in reversed(children)
                if not child.is_symlink() and child.is_dir()
            )
            pending.append((directory, display, index + 1))
        else:
            matches = [child for child in children if fnmatchcase(child.name, segment)]
            if last:
                yield from (display / child.name for child in matches)
            else:
                for child in reversed(matches):
                    target = child
                    if child.is_symlink():
                        if _has_glob_magic(segment):
                            continue
                        try:
                            target = resolve_scoped(str(child), root)
                        except ToolError:
                            budget.failures += 1
                            outside(display / child.name)
                            continue
                        if SKIP_DIRECTORIES.intersection(target.relative_to(root).parts):
                            continue
                    if target.is_dir():
                        pending.append((target, display / child.name, index + 1))


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
    paths: Sequence[str],
    scope: Path | None = None,
    *,
    recursive: bool = False,
    guard: SecurityGuard,
    check_cancelled: Callable[[], None] = lambda: None,
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
    candidates, with a separate allowance of twice that many directory opens and entries examined
    (including empty directories and nonmatches). Glob expansion prunes skip-listed directories. Reading stops as
    soon as `FILES_JUDGE.files_max` survivors exist, and at most `FILES_JUDGE.skip_rows_max` skip
    rows are listed individually before the overflow collapses into one aggregate row per reason —
    count plus the first few paths. Nothing past a bound is read: an unexpanded or unread candidate
    is part of an aggregate, never a judgment.
    """
    started = time.monotonic()
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

    budget = _Discovery(
        2 * FILES_JUDGE.discovery_max,
        lambda path: skip(path.relative_to(root).as_posix(), "unreadable_directory"),
        check_cancelled,
        transitions_left=2 * FILES_JUDGE.discovery_max,
    )
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
        try:
            resolved = resolve_scoped(str(path), root)
        except ToolError:
            skip(text, "outside_scope")
            return True
        if resolved in seen:
            return True
        if traversed and any(part in SKIP_DIRECTORIES for part in resolved.relative_to(root).parts):
            return True
        seen.add(resolved)
        candidates.append((resolved, text))
        return True

    for entry in paths:
        check_cancelled()
        if budget.stopped is not None:
            break
        failures_before = budget.failures
        if _has_glob_magic(entry):
            pattern = _glob_pattern(entry)
            if pattern is None:
                skip(entry, "outside_scope")
                continue
            matched_any = False
            full = False
            for match in _glob_files(
                root, pattern.parts, budget, lambda path: skip(path.relative_to(root).as_posix(), "outside_scope")
            ):
                matched_any = True
                if match.is_dir():
                    try:
                        directory = resolve_scoped(match.relative_to(root).as_posix(), scope=root)
                    except ToolError:
                        skip(match.relative_to(root).as_posix(), "outside_scope")
                        continue
                    if any(part in SKIP_DIRECTORIES for part in directory.relative_to(root).parts):
                        continue
                    for discovered in _walk(directory, budget, recursive=recursive):
                        display = match.relative_to(root) / discovered.relative_to(directory)
                        if not discover(discovered, display.as_posix(), traversed=True):
                            full = True
                            break
                elif match.is_file():
                    if not discover(match, match.relative_to(root).as_posix(), traversed=True):
                        full = True
                if full:
                    break
            if not matched_any and budget.stopped is None and budget.failures == failures_before:
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
            for discovered in _walk(resolved, budget, recursive=recursive):
                walked_any = True
                if not discover(discovered, discovered.relative_to(root).as_posix(), traversed=True):
                    break
            if not walked_any and budget.stopped is None and budget.failures == failures_before:
                skip(entry, "not_found")  # the directory exists but holds nothing to judge
        else:
            discover(resolved, entry, traversed=False)

    survivors: list[tuple[str, str]] = []
    cap_overflow = 0
    cap_first: list[str] = []
    for resolved, text in candidates:
        check_cancelled()
        if len(survivors) == FILES_JUDGE.files_max:
            # Reading stops here: everything past the cap is one aggregate row, not a row per file.
            cap_overflow += 1
            if len(cap_first) < _FIRST_SHOWN:
                cap_first.append(text)
            continue
        try:
            content = read_state(resolved, guard, caps=FILES_JUDGE)
        except ToolError as error:
            skip(text, _SKIP_REASONS[error.code])
            continue
        if not content:
            skip(text, "empty")
            continue
        survivors.append((text, content))
    if cap_overflow:
        skipped.append(_aggregate("over_the_file_cap", cap_overflow, cap_first))
    if budget.stopped is not None:
        skipped.append(
            {
                "path": f"directory scan budget exhausted at {budget.stopped.relative_to(root)}",
                "reason": "over_the_file_cap",
            }
        )
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
    logger.debug(
        "file discovery examined=%d truncated=%d unreadable=%d duration_ms=%.3f",
        2 * FILES_JUDGE.discovery_max - budget.remaining,
        int(budget.stopped is not None or bool(discovery_overflow or cap_overflow)),
        budget.failures,
        1000 * (time.monotonic() - started),
    )
    return survivors, skipped


async def handle(args: dict[str, Any], runtime: Runtime) -> ToolResult:
    kind: str = args["kind"]
    question = build_question(kind, args["instructions"], args["criteria"])
    guard = await runtime.security_guard()
    survivors, skipped = await anyio.to_thread.run_sync(
        lambda: plan(
            args["paths"],
            recursive=bool(args.get("recursive", False)),
            guard=guard,
            check_cancelled=anyio.from_thread.check_cancelled,
        )
    )

    questions = {QUESTION_ID: question}
    states = [{"path": guard.scrub(path), "content": content} for path, content in survivors]
    evaluations: list[Evaluation | None] = [None] * len(survivors)
    failures: list[tuple[int, dict[str, str]]] = []
    refusals: list[tuple[int, Exception]] = []

    async def judge(index: int) -> None:
        path_text, _ = survivors[index]
        try:
            async with runtime.file_fanout:
                evaluations[index] = await runtime.ask(states[index], questions)
        except Exception as error:
            code = code_of(error)
            if isinstance(error, ProviderConfigError) or code == "input_too_large":
                refusals.append((index, error))
                task_group.cancel_scope.cancel()
            else:
                failures.append((index, {"path": path_text, "reason": f"call_failed:{code}"}))

    async with anyio.create_task_group() as task_group:
        for index in range(len(survivors)):
            task_group.start_soon(judge, index)
    if refusals:
        raise min(refusals, key=lambda refusal: refusal[0])[1]

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
    return ToolResult(
        frame(
            "jev_files_judge",
            combined(asked) if asked else None,
            body,
            model=runtime.model,
            provider=runtime.provider_name if survivors else "none",
        ),
        unresolved=not results or any(result["status"] != "ok" for result in results),
    )


TOOL = JevTool(DEFINITION, handle, {"kind": KIND_REFINEMENT})
