# pyright: reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnknownArgumentType=false
"""Harness-agnostic CLI. One JSON object in, one DecisionResult out.

``judge`` calls an existing tool. ``gate`` reads a git range, a claims file, and a tests
log from the repo and calls ``jev_gate``. Neither path branches on a harness.
"""

from __future__ import annotations

import os
import shlex
import stat
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import anyio

from jev_judge_mcp.credential_literal import redact_credential_literals
from jev_judge_mcp.domain.json import decode_json, is_json_object
from jev_judge_mcp.errors import Redactor
from jev_judge_mcp.git_diff import GitDiffError, GitDiffTooLarge, repo_root
from jev_judge_mcp.hook import fail_open_or_ask, hook_required
from jev_judge_mcp.hook_render import render_decision
from jev_judge_mcp.identity import reported_version
from jev_judge_mcp.keyfile import redaction_values, stored_key_path
from jev_judge_mcp.limits import GATE
from jev_judge_mcp.policy import POLICY_VERSION
from jev_judge_mcp.providers import JevProvider
from jev_judge_mcp.serialize import stringify
from jev_judge_mcp.settings import load_settings
from jev_judge_mcp.text import length
from jev_judge_mcp.tools import TOOLS, Runtime, Toolset, gate_range
from jev_judge_mcp.tools.base import ToolError, code_of
from jev_judge_mcp.tools.file_state import is_secret_store, pinned_directory, resolve_scoped
from jev_judge_mcp.tools.toolset import ToolOutcome

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
    return _run_tool("jev_gate", arguments, provider=provider, tests_read_from_file=True)


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
    settings = load_settings()
    key = stored_key_path(settings)
    redact = Redactor(redaction_values(settings))
    try:
        claims_text = _read_evidence(options["claims"], repo, key, GATE.aggregate_evidence_units)
        tests_text = _read_evidence(options["tests"], repo, key, GATE.doc_units)
        request, claims = _claims(claims_text, options.get("request"))
        diff, skipped = gate_range.read_range(options["diff"], key, redact, cwd=repo)
        if skipped:
            raise CliError("secret_file", "range includes a withheld file; review it locally", exit_code=2)
    except ToolError as error:
        raise CliError(
            error.code if error.code == "secret_file" else "invalid_arguments", str(error), exit_code=2
        ) from error
    except GitDiffTooLarge as error:
        raise CliError("input_too_large", str(error), exit_code=2) from error
    except GitDiffError as error:
        raise CliError("invalid_arguments", str(error), exit_code=2) from error
    # The hash is set here because this reader read the file (ADR-0067). The MCP tool does not hash
    # a string the caller typed.
    tests_text = redact(redact_credential_literals(tests_text))
    return {
        "request": redact(redact_credential_literals(request)),
        "diff": diff,
        "claims": [redact(redact_credential_literals(claim)) for claim in claims],
        "evidence": [{"id": "cli", "text": "Read by jev-judge-mcp gate from the local repo."}],
        "tests": tests_text,
    }


def _repo_root() -> Path:
    try:
        return repo_root(Path.cwd())
    except GitDiffError as error:
        raise CliError("invalid_arguments", str(error), exit_code=2) from error


def _read_evidence(name: str, repo: Path, key: Path, units: int) -> str:
    """CLI evidence is strict UTF-8. Validate and read the same bounded file descriptor."""
    path = resolve_scoped(name, repo)
    if is_secret_store(path, key):
        raise CliError("secret_file", "refusing a credential store", exit_code=2)
    ceiling = 3 * units  # maximum UTF-8 bytes per UTF-16 unit
    try:
        with pinned_directory(path.parent) as directory:
            fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode):
                raise CliError("invalid_arguments", "expected a regular evidence file", exit_code=2)
            try:
                key_info = key.stat()
            except FileNotFoundError:
                pass
            else:
                if os.path.samestat(info, key_info):
                    raise CliError("secret_file", "refusing the active key store", exit_code=2)
            if info.st_size > ceiling:
                raise CliError("input_too_large", f"evidence exceeds {units:,} units", exit_code=2)
            with os.fdopen(fd, "rb", closefd=False) as handle:
                data = handle.read(ceiling + 1)
        finally:
            os.close(fd)
        if len(data) > ceiling:
            raise CliError("input_too_large", f"evidence exceeds {units:,} units", exit_code=2)
        content = data.decode("utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise CliError("invalid_arguments", "could not read a UTF-8 evidence file", exit_code=2) from error
    if length(content) > units:
        raise CliError("input_too_large", f"evidence exceeds {units:,} units", exit_code=2)
    if "\x00" in content:
        raise CliError("invalid_arguments", "binary evidence file", exit_code=2)
    return content


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


def _run_tool(
    name: str, arguments: Mapping[str, object], *, provider: JevProvider | None, tests_read_from_file: bool = False
) -> Decision:
    known = {tool.name for tool in TOOLS}
    if name not in known:
        return _failure("invalid_arguments", f"unknown tool {name}", tool=name, exit_code=2)
    try:
        outcome = anyio.run(_execute, name, dict(arguments), provider, tests_read_from_file)
    except Exception as error:
        return _failure(code_of(error), str(error), tool=name)
    if outcome.is_error:
        body: object = outcome.payload if outcome.payload is not None else outcome.text
        return _failure(outcome.error_code or "internal", outcome.text, tool=name, payload=body)
    payload = outcome.payload if outcome.payload is not None else {"text": outcome.text}
    codes = payload.get("reason_codes")
    return _success(
        tool=name,
        action=outcome.action,
        reason_codes=list(codes) if isinstance(codes, list) else [],
        confidence=_confidence(payload),
        usage=payload.get("usage"),
        payload=payload,
        unresolved=outcome.unresolved,
    )


async def _execute(
    name: str, arguments: dict[str, Any], provider: JevProvider | None, tests_read_from_file: bool
) -> ToolOutcome:
    toolset = Toolset(_runtime(provider, tests_read_from_file=tests_read_from_file), TOOLS)
    try:
        return await toolset.execute(name, arguments)
    finally:
        await toolset.aclose()


def _runtime(provider: JevProvider | None, *, tests_read_from_file: bool) -> Runtime:
    settings = load_settings()
    if provider is None:
        return Runtime(settings, tests_read_from_file=tests_read_from_file)
    return Runtime(settings, provider_factory=lambda _settings: provider, tests_read_from_file=tests_read_from_file)


def _emit(decision: Decision) -> int:
    if decision.stderr:
        sys.stderr.write(decision.stderr)
    if decision.rendered:
        sys.stdout.write(decision.rendered)
    return decision.exit_code


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


_SHELL_OPERATORS = frozenset({"&&", "||", ";", ";;", "|", "&"})
"""The operators that end one simple command and start the next, as `shlex` with punctuation reads them."""

_GIT_OPTIONS_WITH_VALUE = frozenset({"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path"})
_GIT_FLAGS = frozenset(
    {"-p", "--paginate", "-P", "--no-pager", "--bare", "--no-replace-objects", "--literal-pathspecs"}
)
"""Git's global options, which sit between `git` and its subcommand (`git -C repo push`)."""


def completion_matches(command: str) -> bool:
    """True only when some simple command on the line is a completion command. Not a general shell match.

    The line is read as the shell reads it (ADR-0064 amendment): `cd repo && git push` is two
    commands and the second matches, `git  push` (extra whitespace) matches, `git pushback` does
    not, and git's global options before the subcommand (`git -C repo push`) are skipped. A line
    that does not tokenize abstains: the shell could not run it either, so there is nothing to gate.
    """
    try:
        tokens = list(shlex.shlex(command, posix=True, punctuation_chars=True))
    except ValueError:
        return False
    argv: list[str] = []
    for token in [*tokens, ";"]:
        if token in _SHELL_OPERATORS or all(character in "&|;" for character in token):
            if argv and _is_completion_command(tuple(argv)):
                return True
            argv = []
        else:
            argv.append(token)
    return False


def _is_completion_command(argv: tuple[str, ...]) -> bool:
    if argv[0] == "git":
        argv = ("git", *_after_git_options(argv[1:]))
    return any(argv[: len(prefix)] == prefix for prefix in _COMPLETION_COMMANDS)


def _after_git_options(rest: tuple[str, ...]) -> tuple[str, ...]:
    index = 0
    while index < len(rest):
        token = rest[index]
        if token in _GIT_OPTIONS_WITH_VALUE:
            index += 2
        elif token in _GIT_FLAGS or (token.startswith("--") and "=" in token):
            index += 1
        else:
            break
    return rest[index:]


def completion_range(
    env: Mapping[str, str], run: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run
) -> str | None:
    """The range the completion gate judges: `JEV_COMPLETION_DIFF` when set, else what a push sends.

    Without the variable, the first range git resolves is used: the commits ahead of the upstream
    (`@{upstream}..HEAD`), else the last commit (`HEAD~1..HEAD`), else the working tree against a
    root commit (`HEAD`). A range that resolves and is empty means nothing is being pushed, so
    there is nothing to gate: `None`. When none resolves, `HEAD` is returned and the gate reports
    what git says (ADR-0064 amendment).
    """
    explicit = env.get("JEV_COMPLETION_DIFF")
    if explicit:
        return explicit
    for candidate in ("@{upstream}..HEAD", "HEAD~1..HEAD", "HEAD"):
        # `--quiet`: exit 1 when the range has differences, 0 when it is empty, 128 when it does not resolve.
        result = run(["git", "diff", "--quiet", candidate], check=False, capture_output=True)
        if result.returncode == 1:
            return candidate
        if result.returncode == 0:
            return None
    return "HEAD"


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
    claims = env.get("JEV_COMPLETION_CLAIMS")
    tests = env.get("JEV_COMPLETION_TESTS")
    if not claims or not tests:
        return fail_open_or_ask(required, "error.code=invalid_arguments\n", "invalid_arguments")
    diff = completion_range(env)
    if diff is None:
        # The push sends no commits: there is no completion to judge, under either flag setting.
        sys.stderr.write("jev-judge-mcp completion-hook: nothing to gate; the push sends no commits\n")
        return 0
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
