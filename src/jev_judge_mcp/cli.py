# pyright: reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnknownArgumentType=false
"""Harness-agnostic CLI. One JSON object in, one DecisionResult out.

``judge`` calls an existing tool. ``gate`` reads a git range, a claims file, and a tests
log from the repo and calls ``jev_gate``. Neither path branches on a harness.
"""

from __future__ import annotations

import hashlib
import os
import shlex
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import anyio

from jev_judge_mcp.domain.json import decode_json, is_json_object
from jev_judge_mcp.hook import fail_open_or_ask, hook_required
from jev_judge_mcp.hook_compact import judge, parse_turns, render_context, window
from jev_judge_mcp.hook_render import render_decision
from jev_judge_mcp.identity import reported_version
from jev_judge_mcp.keyfile import stored_key_path
from jev_judge_mcp.policy import POLICY_VERSION, worst_action
from jev_judge_mcp.policy.actions import Action
from jev_judge_mcp.providers import JevProvider, ProviderConfigError, resolve_model, resolve_provider
from jev_judge_mcp.serialize import stringify
from jev_judge_mcp.settings import load_settings
from jev_judge_mcp.tools import TOOLS, Runtime, Toolset
from jev_judge_mcp.tools.toolset import ToolOutcome, code_of

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


@dataclass(frozen=True, slots=True)
class Decision:
    """One judge or gate result, in process. `rendered` is the DecisionResult JSON line, not yet written.

    The name is Decision, not envelope: Envelope is the provider reply (CONTEXT.md). Wire keys are
    unchanged (ADR-0064).
    """

    exit_code: int
    rendered: str = ""
    stderr: str = ""
    action: str | None = None
    error_code: str | None = None
    unresolved: bool = True


def judge_main(argv: Sequence[str], *, text: str | None = None, provider: JevProvider | None = None) -> int:
    if len(argv) != 1 or not argv[0] or argv[0].startswith("-"):
        sys.stderr.write(_USAGE_JUDGE)
        return 2
    body = sys.stdin.read() if text is None else text
    try:
        parsed = decode_json(body) if body.strip() else None
    except ValueError:
        return _emit(_failure("invalid_arguments", "stdin was not one JSON object", tool=argv[0], exit_code=2))
    if not is_json_object(parsed):
        return _emit(_failure("invalid_arguments", "stdin was not one JSON object", tool=argv[0], exit_code=2))
    return _emit(_run_tool(argv[0], parsed, provider=provider))


def gate_main(argv: Sequence[str], *, provider: JevProvider | None = None) -> int:
    return _emit(run_gate(argv, provider=provider))


def run_gate(argv: Sequence[str], *, provider: JevProvider | None = None) -> Decision:
    """In-process gate. Does not write stdout. The completion hook reads this; `gate_main` emits it."""
    try:
        options = _gate_options(argv)
    except CliError as error:
        return Decision(error.exit_code, stderr=f"jev-judge-mcp gate: {error}\n")
    try:
        arguments = _gate_arguments(options)
    except CliError as error:
        return _failure(error.code, str(error), tool="jev_gate", exit_code=error.exit_code)
    return _run_tool("jev_gate", arguments, provider=provider)


def calibrate_main(argv: Sequence[str]) -> int:
    """Advisory threshold fitting on the caller's labeled rows (ADR-0070); offline, no provider."""
    from jev_judge_mcp import calibrate

    return calibrate.main(argv)


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
    try:
        claims_text = claims_path.read_text(encoding="utf-8")
        tests_bytes = tests_path.read_bytes()
        tests_text = tests_bytes.decode("utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise CliError("invalid_arguments", "could not read a local file", exit_code=2) from error
    request, claims = _claims(claims_text, options.get("request"))
    diff = _git_diff(repo, options["diff"])
    files = _split_unified(diff)
    # The hash is set here because this reader read the file (ADR-0067). The MCP tool does not hash
    # a string the caller typed.
    return {
        "request": request,
        "diff": files if files is not None else diff,
        "claims": claims,
        "evidence": [{"id": "cli", "text": "Read by jev-judge-mcp gate from the local repo."}],
        "tests": tests_text,
        "tests_sha256": hashlib.sha256(tests_bytes).hexdigest(),
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


def _run_tool(name: str, arguments: Mapping[str, object], *, provider: JevProvider | None) -> Decision:
    known = {tool.name for tool in TOOLS}
    if name not in known:
        return _failure("invalid_arguments", f"unknown tool {name}", tool=name, exit_code=2)
    try:
        outcome = anyio.run(_execute, name, dict(arguments), provider)
    except Exception as error:
        return _failure(code_of(error), str(error), tool=name)
    if outcome.is_error:
        body: object = outcome.payload if outcome.payload is not None else outcome.text
        return _failure(outcome.error_code or "provider", outcome.text, tool=name, payload=body)
    payload = outcome.payload if outcome.payload is not None else {"text": outcome.text}
    action, unresolved = _reported(name, outcome, payload)
    codes = payload.get("reason_codes")
    return _success(
        tool=name,
        action=action,
        reason_codes=list(codes) if isinstance(codes, list) else [],
        confidence=_confidence(payload),
        usage=payload.get("usage"),
        payload=payload,
        unresolved=unresolved,
    )


async def _execute(name: str, arguments: dict[str, Any], provider: JevProvider | None) -> ToolOutcome:
    toolset = Toolset(_runtime(provider), TOOLS)
    try:
        return await toolset.execute(name, arguments)
    finally:
        await toolset.aclose()


def _runtime(provider: JevProvider | None) -> Runtime:
    settings = load_settings()
    if provider is None:
        return Runtime(settings)
    return Runtime(settings, provider_factory=lambda _settings: provider)


def _emit(decision: Decision) -> int:
    if decision.stderr:
        sys.stderr.write(decision.stderr)
    if decision.rendered:
        sys.stdout.write(decision.rendered)
    return decision.exit_code


_ACTIONS: tuple[Action, ...] = ("auto", "review", "escalate")
"""The Action vocabulary a payload decision reports (ADR-0064 amendment)."""


def _top_action(payload: object) -> tuple[str | None, bool]:
    """jev_gate and jev_review: the payload's own top-level `action`."""
    action = payload.get("action") if isinstance(payload, dict) else None
    if not isinstance(action, str):
        return None, True
    return action, action != "auto"


def _screen_action(payload: object) -> tuple[str | None, bool]:
    """jev_screen: `recommendation.action`; only `pass` is the green light."""
    recommendation = payload.get("recommendation") if isinstance(payload, dict) else None
    action = recommendation.get("action") if isinstance(recommendation, dict) else None
    if not isinstance(action, str):
        return None, True
    return action, action != "pass"


def _row_action(key: str) -> Callable[[object], tuple[str | None, bool]]:
    """jev_verify and jev_classify: the worst per-row `action`/`decision`."""

    def decide(payload: object) -> tuple[str | None, bool]:
        rows = payload.get("results") if isinstance(payload, dict) else None
        values = [row.get(key) for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []
        actions: list[Action] = []
        for value in values:
            if value in _ACTIONS:
                actions.append(value)
        if not actions:
            return None, True
        action = worst_action(actions)
        return action, action != "auto"

    return decide


def _compare_action(payload: object) -> tuple[str | None, bool]:
    """jev_compare: `overall.decision`; per-aspect decisions are not the headline (ADR-0013)."""
    overall = payload.get("overall") if isinstance(payload, dict) else None
    decision = overall.get("decision") if isinstance(overall, dict) else None
    if decision not in ("auto", "review"):
        return None, True
    return decision, decision != "auto"


def _status_clean(payload: object) -> tuple[str | None, bool]:
    """jev_find and jev_rerank: no action vocabulary; only `invalid_response` is unresolved."""
    status = payload.get("status") if isinstance(payload, dict) else None
    return None, status == "invalid_response"


def _score_clean(payload: object) -> tuple[str | None, bool]:
    """jev_score: no action vocabulary; resolved only on `ok`."""
    status = payload.get("status") if isinstance(payload, dict) else None
    return None, status != "ok"


def _decide_clean(payload: object) -> tuple[str | None, bool]:
    """jev_decide: no action vocabulary; unresolved when no candidate was selected or an escape hatch won."""
    recommendation = payload.get("recommendation") if isinstance(payload, dict) else None
    if not isinstance(recommendation, dict):
        return None, True
    return None, recommendation.get("selected") is None or recommendation.get("escaped") is True


TOOL_DECISIONS: Mapping[str, Callable[[object], tuple[str | None, bool]]] = {
    "jev_verify": _row_action("action"),
    "jev_screen": _screen_action,
    "jev_find": _status_clean,
    "jev_classify": _row_action("decision"),
    "jev_decide": _decide_clean,
    "jev_rerank": _status_clean,
    "jev_compare": _compare_action,
    "jev_review": _top_action,
    "jev_gate": _top_action,
    "jev_score": _score_clean,
}
"""Payload decision fields → (`action`, `unresolved`), in registry order.

jev_extract is not here: its two headlines are on the outcome, computed where the rows are
(ADR-0064 amendment). The guard in `tests/unit/test_cli_judge.py` fails when a tool registers
without a mapping and is not extract. Only the mapped green lights — `auto`, screen's `pass`,
a selected jev_decide candidate, find/rerank without `invalid_response`, score's `ok`,
extract's call headline `auto` — resolve."""


def _reported(name: str, outcome: ToolOutcome, payload: object) -> tuple[str | None, bool]:
    """The DecisionResult `action` and `unresolved`. Extract reads the outcome; the rest read payload fields."""
    if name == "jev_extract":
        headlines = outcome.extract
        action = None if headlines is None else headlines.call_action
        return action, action != "auto"
    return _decision(name, payload)


def _decision(name: str, payload: object) -> tuple[str | None, bool]:
    """`action` and `unresolved` from `name`'s own payload field. Not used for jev_extract."""
    resolve = TOOL_DECISIONS.get(name)
    if resolve is None:
        return None, True
    return resolve(payload)


def _confidence(payload: object) -> float | None:
    if not isinstance(payload, dict):
        return None
    value = payload.get("confidence")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return None


def _failure(
    code: str,
    message: str,
    *,
    tool: str,
    exit_code: int = 1,
    payload: object = None,
) -> Decision:
    return Decision(
        exit_code,
        rendered=_render_decision(
            tool=tool,
            action=None,
            reason_codes=[],
            confidence=None,
            error={"code": code, "message": message},
            usage=None,
            payload=payload,
            unresolved=True,
        ),
        action=None,
        error_code=code,
        unresolved=True,
    )


def _success(
    *,
    tool: str,
    action: str | None,
    reason_codes: list[object],
    confidence: float | None,
    usage: object,
    payload: object,
    unresolved: bool,
) -> Decision:
    return Decision(
        0,
        rendered=_render_decision(
            tool=tool,
            action=action,
            reason_codes=reason_codes,
            confidence=confidence,
            error=None,
            usage=usage,
            payload=payload,
            unresolved=unresolved,
        ),
        action=action,
        error_code=None,
        unresolved=unresolved,
    )


def _render_decision(
    *,
    tool: str,
    action: str | None,
    reason_codes: list[object],
    confidence: float | None,
    error: dict[str, str] | None,
    usage: object,
    payload: object,
    unresolved: bool,
) -> str:
    """The DecisionResult JSON line. Keys are the wire keys; the Python name is not envelope."""
    billed = None
    if isinstance(usage, dict):
        inp, out = usage.get("input_tokens"), usage.get("output_tokens")
        if isinstance(inp, (int, float)) and isinstance(out, (int, float)):
            billed = inp + out
    document = {
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
    return stringify(document) + "\n"


def _version() -> str:
    try:
        return reported_version()
    except Exception:
        return "unknown"


_COMPLETION_COMMANDS: tuple[tuple[str, ...], ...] = (
    ("git", "push"),
    ("gh", "pr", "create"),
    ("gh", "pr", "merge"),
)
"""The argv prefixes the completion hook judges, tokenized the way the shell reads the command."""


def completion_matches(command: str) -> bool:
    """True only for the completion commands. Not a general shell match.

    `shlex.split` decides the tokens, so `git  push` (extra whitespace) matches and
    `git pushback` does not. A command that does not tokenize abstains: the shell could not
    run it either, so there is nothing to gate.
    """
    try:
        argv = tuple(shlex.split(command))
    except ValueError:
        return False
    return any(argv[: len(prefix)] == prefix for prefix in _COMPLETION_COMMANDS)


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


_REACHED_PROVIDER = frozenset({"timeout", "quota", "provider"})


def _gate_failure(required: bool, error_code: str | None) -> int:
    """A non-zero gate. Ask only when the flag is set and the provider was never called.

    The code is the outcome's, not a parse of rendered JSON (ADR-0065 amendment).
    """
    code_name = error_code or "provider"
    message = f"error.code={code_name}\n"
    # A missing code is the old unparsed stdout: the provider was not shown to have been called.
    if required and error_code not in _REACHED_PROVIDER:
        return fail_open_or_ask(True, message, code_name)
    sys.stderr.write(message)
    return 0


def _completion_ask(action: str | None) -> str:
    """The ask for a gate that did not return auto. A fact, not a directive.

    A host model reads an imperative in a permissionDecisionReason ("Read action, not verdict.")
    as a possible prompt injection. The decision is the JSON field; the reason only states what
    the gate decided.
    """
    if action == "escalate":
        reason = "Jev completion hook: gate action is escalate."
    elif action == "review":
        reason = "Jev completion hook: gate action is review."
    else:
        reason = "Jev completion hook: gate action is not auto."
    return render_decision("ask", reason) + "\n"


def completion_hook_main(
    argv: Sequence[str],
    *,
    text: str | None = None,
    environ: Mapping[str, str] | None = None,
    provider: JevProvider | None = None,
) -> int:
    """Opt-in completion hook. Empty stdout abstains. Never prints allow.

    A missing credential or a missing local path fails open: exit 0, stderr carries
    ``error.code``, stdout stays empty so it cannot be read as a pass.
    ``JEV_HOOK_REQUIRED=1`` asks instead for bad stdin, missing credentials, a gate error
    coded ``auth`` (a missing key, or an upstream 401 per ADR-0072), and a gate error that
    never reached the provider (ADR-0065). A provider timeout, quota, or other provider
    error stays fail-open. The gate runs in process; this function reads the outcome and
    does not capture or parse stdout.
    """
    if list(argv):
        sys.stderr.write("jev-judge-mcp completion-hook: usage: jev-judge-mcp completion-hook\n")
        return 2
    body = sys.stdin.read() if text is None else text
    env = os.environ if environ is None else environ
    required = hook_required(env)
    try:
        parsed = decode_json(body)
    except ValueError:
        return fail_open_or_ask(required, "error.code=invalid_arguments\n", "stdin was not hook-event JSON")
    if not is_json_object(parsed):
        return fail_open_or_ask(required, "", "stdin was not hook-event JSON")
    if not completion_matches(command_from_hook_event(parsed)):
        return 0
    diff = env.get("JEV_COMPLETION_DIFF", "HEAD")
    claims = env.get("JEV_COMPLETION_CLAIMS")
    tests = env.get("JEV_COMPLETION_TESTS")
    if not claims or not tests:
        return fail_open_or_ask(required, "error.code=invalid_arguments\n", "invalid_arguments")
    try:
        decision = run_gate(["--diff", diff, "--claims", claims, "--tests", tests], provider=provider)
    except Exception:
        return fail_open_or_ask(required, "error.code=provider\n", "provider")
    if decision.exit_code != 0:
        return _gate_failure(required, decision.error_code)
    if decision.action == "auto":
        return 0
    sys.stdout.write(_completion_ask(decision.action))
    return 0


_COMPACT_USAGE = "jev-judge-mcp hook: usage: jev-judge-mcp hook compact-cut\n"
_COMPACT_STDIN = "jev-judge-mcp hook: stdin was not hook-event JSON\n"


def compact_cut_main(
    argv: Sequence[str] | None = None,
    *,
    text: str | None = None,
    provider: JevProvider | None = None,
) -> int:
    """Opt-in compaction cut point (ADR-0077). Empty stdout abstains; there is no ask surface.

    Claude Code fires SessionStart with source ``compact`` after a compaction. The hook clips
    the transcript's user turns, asks one Choice keyed by real turn ids, and a confident pick
    rides back as one additionalContext line. A source other than ``compact``, an unusable
    transcript, or fewer than two turns abstains before any provider work. A provider failure
    is silence plus one ``error.code`` stderr line. The cut point reads no hook variable:
    SessionStart has no ask decision for ``JEV_HOOK_REQUIRED`` to escalate to.
    """
    if list(argv or []) != ["compact-cut"]:
        sys.stderr.write(_COMPACT_USAGE)
        return 2
    body = sys.stdin.read() if text is None else text
    try:
        parsed = decode_json(body)
    except ValueError:
        sys.stderr.write(_COMPACT_STDIN)
        return 0
    if not is_json_object(parsed):
        sys.stderr.write(_COMPACT_STDIN)
        return 0
    if parsed.get("hook_event_name") != "SessionStart" or parsed.get("source") != "compact":
        return 0
    transcript_path = parsed.get("transcript_path")
    if not isinstance(transcript_path, str) or not transcript_path:
        return 0
    try:
        transcript = Path(transcript_path).read_text(encoding="utf-8")
    except (OSError, UnicodeError, ValueError):
        return 0
    turns = window(parse_turns(transcript))
    if len(turns) < 2:
        return 0

    settings = load_settings()
    # The redacting handler before any provider call can log (ADR-0008), the same wiring hook gate
    # uses: a misconfigured environment still gets silence or the one line, never a traceback.
    from jev_judge_mcp.server import configure_logging

    configure_logging(settings.log_level, settings.secret_values())
    model = resolve_model(settings)
    chosen = provider
    if chosen is None:
        try:
            chosen = resolve_provider(settings)
        except ProviderConfigError as error:
            sys.stderr.write(f"jev-judge-mcp hook compact-cut: fail-open ({error})\n")
            return 0
    line, code = judge(chosen, turns, model)
    if line is None:
        if code:
            sys.stderr.write(f"error.code={code}\n")
        return 0
    sys.stdout.write(render_context(line) + "\n")
    return 0
