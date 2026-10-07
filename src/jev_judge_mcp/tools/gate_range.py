"""jev_gate_range: jev_gate over a git range the server reads itself (ADR-0080).

An agent that pastes an excerpt or a paraphrase of its diff into `jev_gate.diff` gets the excerpt
reviewed. This tool takes the range instead: the server resolves the repository from its working
directory, reads `git diff <range>` through the boundary the gate CLI uses (`git_diff.py`), reads an
optional test log through the file tools' scoped reader (`file_state.py`), and hands both to the
jev_gate handler unchanged. There is no second copy of gate logic; every refusal here precedes any
provider call.
"""

import hashlib
from dataclasses import replace
from pathlib import Path
from typing import Any

import anyio

from jev_judge_mcp.credential_literal import redact_credential_literals
from jev_judge_mcp.git_diff import GitDiffError, GitDiffTooLarge, diff_argument, repo_root
from jev_judge_mcp.tools import gate
from jev_judge_mcp.tools.arguments import Refinement
from jev_judge_mcp.tools.base import JevTool, Runtime, ToolError, ToolResult, define
from jev_judge_mcp.tools.file_state import is_secret_store, read_state, refuse, resolve_scoped

NAME = "jev_gate_range"

_GATE = gate.DEFINITION.input_schema["properties"]
_PASSED = ("request", "claims", "evidence", "auto_accept", "review_at", "composite_floor")

DEFINITION = define(
    NAME,
    "Gate completion over a git range the server reads",
    "jev_gate for a change that is in git: name the range and the server reads `git diff <range>` itself, "
    "so the whole patch is reviewed, not an excerpt you typed. The diff is limited to the server's working "
    "directory inside its git repository, and each end of the range must name a commit or a tree. "
    "A changed secret store (.env, keys) is never sent and is listed in "
    "skipped; credential literals in other patches are redacted. "
    "Optional `tests_path` is a test log the server reads, under the same path "
    "rules as jev_file_judge (inside the working directory, no secret stores, no binary files). Everything "
    "else is jev_gate: the same claims, evidence, thresholds, caps, and payload. A bad range, an empty diff, "
    "or no git repository is refused before any provider call. Use jev_gate for a patch that is not "
    "committed or staged in git.",
    {
        "type": "object",
        "properties": {
            "request": _GATE["request"],
            "range": {
                "type": "string",
                "minLength": 1,
                "description": "A git revision range (`main..HEAD`) or one revision (`HEAD~1`), passed to "
                "`git diff`. A leading `-` is refused.",
            },
            "claims": _GATE["claims"],
            "evidence": _GATE["evidence"],
            "tests_path": {
                "type": "string",
                "minLength": 1,
                "description": "A test log for the review, relative to the server's working directory. "
                "The server reads it; a path outside the directory is refused.",
            },
            "auto_accept": _GATE["auto_accept"],
            "review_at": _GATE["review_at"],
            "composite_floor": _GATE["composite_floor"],
        },
        "required": ["request", "range", "claims", "evidence"],
        "additionalProperties": False,
    },
)


async def handle(args: dict[str, Any], runtime: Runtime) -> ToolResult:
    passed: dict[str, Any] = {key: args[key] for key in _PASSED if key in args}
    tests_path: str | None = args.get("tests_path")
    if tests_path is not None:
        tests = read_state(resolve_scoped(tests_path))
        passed["tests"] = tests
        # This reader read the log, so the review weighs it as read, not self-reported (ADR-0067).
        passed["tests_sha256"] = hashlib.sha256(tests.encode("utf-8")).hexdigest()
    try:
        diff = await anyio.to_thread.run_sync(_read_range, args["range"])
    except GitDiffTooLarge as error:
        raise ToolError(str(error), code="input_too_large") from error
    except GitDiffError as error:
        raise ToolError(str(error), code="invalid_arguments") from error
    files, skipped = _guarded(diff)
    passed["diff"] = files
    result = await gate.handle(passed, runtime)
    extra: dict[str, object] = {"skipped": skipped} if skipped else {}
    return replace(result, payload={**result.payload, "tool": NAME, **extra})


def _read_range(rev_range: str) -> str | list[dict[str, str]]:
    """The range's diff limited to the working directory, the file tools' scope (ADR-0077): the
    repository must contain it, and the `.` pathspec keeps every other part of the repository out.
    Run off the event loop so a slow git blocks no other call."""
    cwd = Path.cwd()
    repo_root(cwd)
    return diff_argument(cwd, rev_range, paths=(".",))


def _guarded(diff: str | list[dict[str, str]]) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """The file tools' read guards over server-read patches: a secret store's patch never leaves,
    and every other patch has its credential literals redacted (ADR-0080).

    A diff whose headers do not split is refused, since no per-file guard can be applied to it.
    """
    if isinstance(diff, str):
        raise ToolError(
            "git diff headers did not split into files; use jev_gate for this patch", code="invalid_arguments"
        )
    kept: list[dict[str, str]] = []
    skipped: list[dict[str, str]] = []
    for item in diff:
        if is_secret_store(Path(item["path"])):
            skipped.append({"path": item["path"], "reason": "secret_file"})
        else:
            kept.append({"path": item["path"], "patch": redact_credential_literals(item["patch"])})
    if not kept:
        refuse("secret_file", "every file in that range is a secret store; nothing was sent")
    return kept, skipped


EVIDENCE_NOT_EMPTY = Refinement(
    gate.EVIDENCE_NOT_EMPTY.check,
    f"{NAME} requires at least one evidence item with non-empty text.",
)
"""jev_gate's evidence refinement under this tool's name, so the refusal names the tool called."""

TOOL = JevTool(DEFINITION, handle, {"evidence": EVIDENCE_NOT_EMPTY})
