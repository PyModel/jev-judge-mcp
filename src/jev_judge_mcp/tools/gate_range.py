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

from jev_judge_mcp.credential_literal import has_private_key_boundary, redact_credential_literals
from jev_judge_mcp.errors import Redactor
from jev_judge_mcp.git_diff import GitDiffError, GitDiffTooLarge, diff_argument, repo_root, scoped_range
from jev_judge_mcp.keyfile import redaction_values, stored_key_path
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
    "directory inside its git repository. Use commits, commit:path for a subtree, or the empty tree. "
    "A nonempty tree object without its repository path is refused. "
    "A changed secret store (.env, keys) is never sent and is listed in "
    "skipped. A file with a private-key delimiter in either side is also withheld. Any withheld file "
    "holds the whole range for local review. Credential literals in other "
    "patches are redacted. "
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
    key_path = stored_key_path(runtime.settings)
    redact = Redactor(await anyio.to_thread.run_sync(redaction_values, runtime.settings))
    tests_path: str | None = args.get("tests_path")
    if tests_path is not None:
        tests = await anyio.to_thread.run_sync(
            lambda: read_state(resolve_scoped(tests_path), key_path=key_path, redact=redact)
        )
        passed["tests"] = tests
        # This reader read the log, so the review weighs it as read, not self-reported (ADR-0067).
        passed["tests_sha256"] = hashlib.sha256(tests.encode("utf-8")).hexdigest()
    try:
        files, skipped = await anyio.to_thread.run_sync(
            lambda: read_range(args["range"], key_path, redact, cwd=Path.cwd())
        )
    except GitDiffTooLarge as error:
        raise ToolError(str(error), code="input_too_large") from error
    except GitDiffError as error:
        # The harness picks this directory, not the caller; naming it shows a server launched
        # outside the caller's project (a `uv run --directory` entry) instead of a bare refusal.
        raise ToolError(f"{error} (server working directory: {Path.cwd()})", code="invalid_arguments") from error
    passed["diff"] = files
    result = await gate.handle(passed, runtime, withheld=skipped)
    extra: dict[str, object] = {"skipped": skipped} if skipped else {}
    return replace(result, payload={**result.payload, "tool": NAME, **extra})


def read_range(
    rev_range: str, key_path: Path, redact: Redactor, *, cwd: Path
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """Read and guard the diff inside the caller's scope: the CLI repository root or the MCP working directory."""
    root = repo_root(cwd)
    revision, origins = scoped_range(cwd, rev_range)
    diff = diff_argument(cwd, revision, paths=(".",), flags=("--full-index",))
    # A narrow hunk can contain private-key material without a delimiter. Inspect the
    # complete context locally, within the same byte budget, then send only the normal patch.
    complete = diff_argument(cwd, revision, paths=(".",), flags=("--full-index", "--unified=1000000000"))
    if isinstance(diff, str) or isinstance(complete, str):
        raise ToolError(
            "git diff headers did not split into files; use jev_gate for this patch", code="invalid_arguments"
        )
    kept: list[dict[str, str]] = []
    skipped: list[dict[str, str]] = []
    if len(diff) != len(complete):
        raise GitDiffError("git range changed during inspection")
    for item, inspected in zip(diff, complete, strict=True):
        patch = item["patch"]
        full = inspected["patch"]
        # The index line includes the complete old/new object ids, including working-tree
        # images. A concurrent edit invalidates the inspection instead of changing its subject.
        if item["path"] != inspected["path"] or patch.partition("\n@@")[0] != full.partition("\n@@")[0]:
            raise GitDiffError("git range changed during inspection")
        paths = [root / origin / item["path"] for origin in origins]
        if not paths or any(not path.is_relative_to(cwd) for path in paths):
            raise GitDiffError("git patch origin is outside the working directory")
        label = redact_credential_literals(item["path"], redact)
        if (
            has_private_key_boundary(full)
            or label != item["path"]
            or any(is_secret_store(path, key_path) for path in paths)
        ):
            skipped.append({"path": label, "reason": "secret_file"})
        else:
            kept.append({"path": item["path"], "patch": redact_credential_literals(patch, redact)})
    if not kept:
        paths = ", ".join(item["path"] for item in skipped)
        refuse("secret_file", f"Every file in the range was withheld as a secret store: {paths}. Review locally.")
    return kept, skipped


EVIDENCE_NOT_EMPTY = Refinement(
    gate.EVIDENCE_NOT_EMPTY.check,
    f"{NAME} requires at least one evidence item with non-empty text.",
)
"""jev_gate's evidence refinement under this tool's name, so the refusal names the tool called."""

TOOL = JevTool(DEFINITION, handle, {"evidence": EVIDENCE_NOT_EMPTY})
