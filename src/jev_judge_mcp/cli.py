# pyright: reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnknownArgumentType=false
"""Harness-agnostic CLI. One JSON object in, one DecisionResult out.

``judge`` calls an existing tool. ``gate`` reads a git range, a claims file, and a tests
log from the repo and calls ``jev_gate``. Neither path branches on a harness.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import anyio

from jev_judge_mcp.domain.json import decode_json, is_json_object
from jev_judge_mcp.identity import reported_version
from jev_judge_mcp.keyfile import stored_key_path
from jev_judge_mcp.policy.thresholds import POLICY_VERSION
from jev_judge_mcp.serialize import stringify, stringify_compact
from jev_judge_mcp.settings import load_settings
from jev_judge_mcp.tools import TOOLS, Runtime, Toolset

SCHEMA_VERSION = 1
_USAGE_JUDGE = "jev-judge-mcp judge: usage: jev-judge-mcp judge <tool>\n"
_USAGE_GATE = (
    "jev-judge-mcp gate: usage: jev-judge-mcp gate --diff <git-range> "
    "--claims <file> --tests <file> [--request <text>]\n"
)


class CliError(Exception):
    def __init__(self, code: str, message: str, *, exit_code: int = 1) -> None:
        super().__init__(message)
        self.code = code
        self.exit_code = exit_code


def judge_main(argv: Sequence[str], *, text: str | None = None) -> int:
    if len(argv) != 1 or not argv[0] or argv[0].startswith("-"):
        sys.stderr.write(_USAGE_JUDGE)
        return 2
    body = sys.stdin.read() if text is None else text
    try:
        parsed = decode_json(body) if body.strip() else None
    except ValueError:
        return _fail("invalid_arguments", "stdin was not one JSON object", tool=argv[0], exit_code=2)
    if not is_json_object(parsed):
        return _fail("invalid_arguments", "stdin was not one JSON object", tool=argv[0], exit_code=2)
    return _run_tool(argv[0], parsed)


def gate_main(argv: Sequence[str]) -> int:
    try:
        options = _gate_options(argv)
    except CliError as error:
        sys.stderr.write(f"jev-judge-mcp gate: {error}\n")
        return error.exit_code
    try:
        arguments = _gate_arguments(options)
    except CliError as error:
        return _fail(error.code, str(error), tool="jev_gate", exit_code=error.exit_code)
    return _run_tool("jev_gate", arguments)


def _gate_options(argv: Sequence[str]) -> dict[str, str]:
    if not argv or (argv[0].startswith("-") and argv[0] not in ("--diff", "--claims", "--tests", "--request")):
        if argv and argv[0] in ("-h", "--help"):
            raise CliError("invalid_arguments", _USAGE_GATE.strip(), exit_code=2)
    options: dict[str, str] = {}
    index = 0
    flags = {"--diff", "--claims", "--tests", "--request"}
    while index < len(argv):
        flag = argv[index]
        if flag not in flags or index + 1 >= len(argv):
            raise CliError("invalid_arguments", _USAGE_GATE.strip(), exit_code=2)
        options[flag[2:]] = argv[index + 1]
        index += 2
    missing = {"diff", "claims", "tests"} - options.keys()
    if missing:
        raise CliError("invalid_arguments", _USAGE_GATE.strip(), exit_code=2)
    return options


def _gate_arguments(options: Mapping[str, str]) -> dict[str, object]:
    repo = _repo_root()
    key = stored_key_path(load_settings()).resolve()
    claims_path = _inside_repo(Path(options["claims"]), repo, key)
    tests_path = _inside_repo(Path(options["tests"]), repo, key)
    claims_text = claims_path.read_text(encoding="utf-8")
    request, claims = _claims(claims_text, options.get("request"))
    diff = _git_diff(repo, options["diff"])
    files = _split_unified(diff)
    return {
        "request": request,
        "diff": files if files is not None else diff,
        "claims": claims,
        "evidence": [{"id": "cli", "text": "Read by jev-judge-mcp gate from the local repo."}],
        "tests": tests_path.read_text(encoding="utf-8"),
    }


def _repo_root() -> Path:
    run = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],  # noqa: S607
        check=False,
        capture_output=True,
        text=True,
    )
    if run.returncode != 0 or not run.stdout.strip():
        raise CliError("invalid_arguments", "gate refuses to run outside a git repo", exit_code=2)
    return Path(run.stdout.strip()).resolve()


def _inside_repo(path: Path, repo: Path, key: Path) -> Path:
    candidate = path if path.is_absolute() else repo / path
    if candidate.is_symlink() or any(parent.is_symlink() for parent in candidate.parents):
        resolved = candidate.resolve()
    else:
        resolved = candidate.resolve()
    if not _is_relative_to(resolved, repo):
        raise CliError("invalid_arguments", f"refusing path outside the repo: {path}", exit_code=2)
    if resolved == key:
        raise CliError("invalid_arguments", "refusing the key file", exit_code=2)
    if not resolved.is_file():
        raise CliError("invalid_arguments", f"not a file: {path}", exit_code=2)
    return resolved


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _claims(text: str, request: str | None) -> tuple[str, list[str]]:
    stripped = text.strip()
    if stripped.startswith("{"):
        try:
            parsed = decode_json(stripped)
        except ValueError as error:
            raise CliError("invalid_arguments", "claims file was not JSON", exit_code=2) from error
        if not is_json_object(parsed):
            raise CliError("invalid_arguments", "claims file needs a claims array", exit_code=2)
        raw_claims = parsed.get("claims")
        if not isinstance(raw_claims, list):
            raise CliError("invalid_arguments", "claims file needs a claims array", exit_code=2)
        claims = [item for item in raw_claims if isinstance(item, str) and item]
        found = parsed.get("request") if isinstance(parsed.get("request"), str) else request
        if not isinstance(found, str) or not found or not claims:
            raise CliError("invalid_arguments", "claims file needs request and claims", exit_code=2)
        return found, claims
    claims = [line for line in text.splitlines() if line.strip()]
    if request is None or not request.strip() or not claims:
        raise CliError("invalid_arguments", "plain claims need --request and one claim per line", exit_code=2)
    return request, claims


def _git_diff(repo: Path, rev_range: str) -> str:
    if not rev_range or rev_range.startswith("-") or "\x00" in rev_range:
        raise CliError("invalid_arguments", "refusing that git range", exit_code=2)
    run = subprocess.run(  # noqa: S603 - range is checked above; git is a fixed argv0
        ["git", "-C", str(repo), "diff", rev_range],  # noqa: S607
        check=False,
        capture_output=True,
        text=True,
    )
    if run.returncode != 0:
        raise CliError("invalid_arguments", "git diff failed", exit_code=2)
    if not run.stdout.strip():
        raise CliError("invalid_arguments", "git diff was empty", exit_code=2)
    return run.stdout


def _split_unified(diff: str) -> list[dict[str, str]] | None:
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
    first = chunk.splitlines()[0]
    marker = " b/"
    if marker not in first:
        return None
    path = first.rsplit(marker, 1)[-1]
    if not path or path.startswith("/") or ".." in Path(path).parts:
        return None
    return path


def _run_tool(name: str, arguments: Mapping[str, object]) -> int:
    known = {tool.name for tool in TOOLS}
    if name not in known:
        return _fail("invalid_arguments", f"unknown tool {name}", tool=name, exit_code=2)
    try:
        result = anyio.run(_call, name, dict(arguments))
    except Exception as error:
        return _fail(_code_for(error), str(error), tool=name)
    text = result.content[0].text if result.content else ""
    if result.is_error:
        code = _code_for_text(text)
        return _fail(code, text, tool=name, payload_text=text)
    try:
        payload = json.loads(text)
    except ValueError:
        payload = {"text": text}
    action = payload.get("action") if isinstance(payload, dict) else None
    codes = payload.get("reason_codes") if isinstance(payload, dict) else None
    _write_envelope(
        tool=name,
        action=action if isinstance(action, str) else None,
        reason_codes=list(codes) if isinstance(codes, list) else [],
        confidence=_confidence(payload),
        error=None,
        usage=payload.get("usage") if isinstance(payload, dict) else None,
        payload=payload,
        unresolved=action not in ("auto", "pass"),
    )
    return 0


async def _call(name: str, arguments: dict[str, Any]) -> Any:
    toolset = Toolset(Runtime(load_settings()), TOOLS)
    try:
        return await toolset.call(name, arguments)
    finally:
        await toolset.aclose()


def _confidence(payload: object) -> float | None:
    if not isinstance(payload, dict):
        return None
    value = payload.get("confidence")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return None


def _fail(
    code: str,
    message: str,
    *,
    tool: str,
    exit_code: int = 1,
    payload_text: str | None = None,
) -> int:
    payload: object = None
    if payload_text:
        try:
            payload = json.loads(payload_text)
        except ValueError:
            payload = payload_text
    _write_envelope(
        tool=tool,
        action=None,
        reason_codes=[],
        confidence=None,
        error={"code": code, "message": message},
        usage=None,
        payload=payload,
        unresolved=True,
    )
    return exit_code


def _write_envelope(
    *,
    tool: str,
    action: str | None,
    reason_codes: list[object],
    confidence: float | None,
    error: dict[str, str] | None,
    usage: object,
    payload: object,
    unresolved: bool,
) -> None:
    billed = None
    if isinstance(usage, dict):
        inp, out = usage.get("input_tokens"), usage.get("output_tokens")
        if isinstance(inp, (int, float)) and isinstance(out, (int, float)):
            billed = inp + out
    envelope = {
        "schema_version": SCHEMA_VERSION,
        "policy_version": POLICY_VERSION,
        "server_version": _version(),
        "tool": tool,
        "action": action,
        "reason_codes": reason_codes,
        "confidence": confidence,
        "error": error,
        "usage": usage,
        "billed_tokens": billed,
        "unresolved": unresolved,
        "payload": payload,
    }
    sys.stdout.write(stringify(envelope) + "\n")


def _version() -> str:
    try:
        return reported_version()
    except Exception:
        return "unknown"


def _code_for(error: BaseException) -> str:
    name = type(error).__name__
    if name in ("ProviderConfigError",):
        return "auth"
    if name in ("ProviderTimeoutError",):
        return "timeout"
    if getattr(error, "status", None) == 429:
        return "quota"
    if name == "ArgumentsError":
        return "invalid_arguments"
    return "provider"


def _code_for_text(text: str) -> str:
    if text.startswith("No Jev provider credentials") or "credentials" in text[:80]:
        return "auth"
    if text.startswith("MCP error -32602"):
        return "invalid_arguments"
    if "aggregate budget" in text or "exceeds" in text:
        return "input_too_large"
    if "timed out" in text or "timeout" in text.lower():
        return "timeout"
    if "429" in text or "rate" in text.lower():
        return "quota"
    return "provider"


def completion_matches(command: str) -> bool:
    """True only for the completion commands. Not a general shell match."""
    stripped = command.strip()
    return stripped.startswith(("git push", "gh pr create", "gh pr merge"))


def command_from_hook_event(event: Mapping[str, object]) -> str:
    tool_input = event.get("tool_input")
    if isinstance(tool_input, str):
        return tool_input
    if isinstance(tool_input, Mapping):
        for key in ("command", "cmd"):
            value = tool_input.get(key)
            if isinstance(value, str):
                return value
    return ""


def completion_hook_main(
    argv: Sequence[str], *, text: str | None = None, environ: Mapping[str, str] | None = None
) -> int:
    """Opt-in completion hook. Empty stdout abstains. Never prints allow.

    A missing credential or a missing local path fails open: exit 0, stderr carries
    ``error.code``, stdout stays empty so it cannot be read as a pass.
    """
    if list(argv):
        sys.stderr.write("jev-judge-mcp completion-hook: usage: jev-judge-mcp completion-hook\n")
        return 2
    body = sys.stdin.read() if text is None else text
    env = os.environ if environ is None else environ
    try:
        parsed = decode_json(body)
    except ValueError:
        sys.stderr.write("error.code=invalid_arguments\n")
        return 0
    if not is_json_object(parsed) or not completion_matches(command_from_hook_event(parsed)):
        return 0
    diff = env.get("JEV_COMPLETION_DIFF", "HEAD")
    claims = env.get("JEV_COMPLETION_CLAIMS")
    tests = env.get("JEV_COMPLETION_TESTS")
    if not claims or not tests:
        sys.stderr.write("error.code=invalid_arguments\n")
        return 0
    saved_out = sys.stdout
    saved_err = sys.stderr
    out, err = io.StringIO(), io.StringIO()
    sys.stdout, sys.stderr = out, err
    try:
        code = gate_main(["--diff", diff, "--claims", claims, "--tests", tests])
    finally:
        sys.stdout, sys.stderr = saved_out, saved_err
    rendered = out.getvalue()
    if code != 0:
        message = "error.code=provider\n"
        try:
            envelope = json.loads(rendered)
            if isinstance(envelope, dict) and isinstance(envelope.get("error"), dict):
                message = f"error.code={envelope['error'].get('code', 'provider')}\n"
        except ValueError:
            pass
        sys.stderr.write(message)
        return 0
    try:
        envelope = json.loads(rendered)
    except ValueError:
        sys.stderr.write("error.code=provider\n")
        return 0
    action = envelope.get("action") if isinstance(envelope, dict) else None
    if action == "auto":
        return 0
    reason = "Jev completion hook: not auto."
    if action == "escalate":
        reason = "Jev completion hook: escalate. Read action, not verdict."
    elif action == "review":
        reason = "Jev completion hook: review. Confirm before proceeding."
    sys.stdout.write(
        stringify_compact(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "ask",
                    "permissionDecisionReason": reason,
                }
            }
        )
        + "\n"
    )
    return 0
