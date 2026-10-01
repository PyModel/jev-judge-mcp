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

from collections.abc import Sequence
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
    "carries the typed answers plus the skipped paths with their reasons — no file's bytes enter your "
    "context. Every read is credential-literal redacted before it is judged. To pick among the answers, run "
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


def _walk(directory: Path, *, recursive: bool) -> list[Path]:
    """The files under `directory`, sorted per directory; skip-listed directories are never entered.

    Non-recursive lists the immediate files only. `Path.walk` prunes in place, so a skip-listed
    subtree is not descended at all.
    """
    if not recursive:
        return sorted(child for child in directory.iterdir() if child.is_file())
    found: list[Path] = []
    for root, directories, files in directory.walk(on_error=lambda _: None):
        directories[:] = sorted(name for name in directories if name not in SKIP_DIRECTORIES)
        found.extend(Path(root, name) for name in sorted(files))
    return found


def plan(
    paths: Sequence[str], scope: Path | None = None, *, recursive: bool = False
) -> tuple[list[tuple[str, str]], list[dict[str, str]]]:
    """Expand, prune, and read — the whole deterministic half of the tool, before any provider call.

    returns the survivors as `(path text, redacted content)` in input-discovery order and every
    skipped path with its stable reason. `scope` is the server's working directory (the production
    default when `None`); tests pass a tmp tree. The check order is the ADR's: scope, the skip list
    on discovered paths, existence, the secret-store name, the binary sniff and the size cap inside
    the read, emptiness, then the hard surviving-files cap. The path text is the caller's entry for
    a directly named file — the same echo `jev_file_judge` makes — and the scope-relative posix
    path for everything discovered.
    """
    root = (scope if scope is not None else Path.cwd()).resolve()
    skipped: list[dict[str, str]] = []

    def skip(path: str, reason: str) -> None:
        skipped.append({"path": path, "reason": reason})

    candidates: list[tuple[Path, str]] = []
    seen: set[Path] = set()

    def discover(path: Path, text: str, *, traversed: bool) -> None:
        """One discovered file, deduplicated by its resolved name.

        A traversed file (a directory walk or a glob match) faces the skip list and the scope check;
        a directly named file was already scoped by `resolve_scoped` and bypasses the skip list —
        the skip list governs traversal, not an explicit name. `text` is the caller's entry for a
        directly named file and the scope-relative posix path for everything discovered, so the
        payload always names a file the way the caller could have.
        """
        resolved = path.resolve()
        if resolved in seen:
            return
        if not resolved.is_relative_to(root):
            skip(path.relative_to(root).as_posix(), "outside_scope")
            return
        if traversed and any(part in SKIP_DIRECTORIES for part in resolved.relative_to(root).parts):
            return
        seen.add(resolved)
        candidates.append((resolved, text))

    for entry in paths:
        if _has_glob_magic(entry):
            pattern = _glob_pattern(entry)
            if pattern is None:
                skip(entry, "outside_scope")
                continue
            matches = sorted(root.glob(pattern.as_posix()))
            if not matches:
                skip(entry, "not_found")
            for match in matches:
                if match.is_dir():
                    for discovered in _walk(match, recursive=recursive):
                        discover(discovered, discovered.relative_to(root).as_posix(), traversed=True)
                elif match.is_file():
                    discover(match, match.relative_to(root).as_posix(), traversed=True)
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
            walked = _walk(resolved, recursive=recursive)
            for discovered in walked:
                discover(discovered, discovered.relative_to(root).as_posix(), traversed=True)
            if not walked:
                skip(entry, "not_found")  # the directory exists but holds nothing to judge
        else:
            discover(resolved, entry, traversed=False)

    survivors: list[tuple[str, str]] = []
    for resolved, text in candidates:
        try:
            content = read_state(resolved, caps=FILES_JUDGE)
        except ToolError as error:
            skip(text, _SKIP_REASONS[error.code])
            continue
        except OSError:
            skip(text, "not_found")
            continue
        if not content:
            skip(text, "empty")
            continue
        survivors.append((text, content))
    for text, _ in survivors[FILES_JUDGE.files_max :]:
        skip(text, "over_the_file_cap")
    return survivors[: FILES_JUDGE.files_max], skipped


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
