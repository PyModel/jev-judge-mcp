# pyright: reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnknownArgumentType=false
"""Harness-agnostic CLI. One JSON object in, one DecisionResult out.

``judge`` calls an existing tool. ``gate`` reads a git range, a claims file, and a tests
log from the repo and calls ``jev_gate``. Neither path branches on a harness.
"""

from __future__ import annotations

import os
import re
import shlex
import subprocess
import sys
import time
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import anyio
from pydantic import ValidationError

from jev_judge_mcp.domain.json import decode_json, is_json_object
from jev_judge_mcp.git import GitDiffError, GitDiffTooLarge, range_has_changes, repo_root, run
from jev_judge_mcp.hook import fail_open_or_ask, hook_required
from jev_judge_mcp.hook_render import render_decision
from jev_judge_mcp.identity import reported_version
from jev_judge_mcp.limits import FILE_JUDGE, GATE
from jev_judge_mcp.policy import POLICY_VERSION
from jev_judge_mcp.process import OutputTooLarge
from jev_judge_mcp.providers import JevProvider
from jev_judge_mcp.redaction.action import redact_action
from jev_judge_mcp.redaction.values import register_log_secrets
from jev_judge_mcp.security import SecurityGuard, build_guard
from jev_judge_mcp.serialize import stringify
from jev_judge_mcp.settings import load_settings
from jev_judge_mcp.tools import TOOLS, Runtime, Toolset, gate_range
from jev_judge_mcp.tools.base import ToolError, code_of
from jev_judge_mcp.tools.file_state import read_state, resolve_scoped
from jev_judge_mcp.tools.toolset import ToolOutcome

COMPLETION_HARNESS_TIMEOUT_SECONDS = 90.0
"""Command timeout in the shipped completion hook fragment."""

COMPLETION_PROCESS_MARGIN_SECONDS = 10.0
"""Time reserved for interpreter startup, cleanup, and emitting the hook response."""

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


def run_gate(
    argv: Sequence[str],
    *,
    provider: JevProvider | None = None,
    deadline: float | None = None,
    repo: Path | None = None,
    shell_cwd: Path | None = None,
) -> Decision:
    """In-process gate. Does not write stdout. The completion hook reads this; `gate_main` emits it."""
    try:
        options = _gate_options(argv)
    except CliError as error:
        return Decision(error.exit_code, stderr=f"jev-judge-mcp gate: {error}\n")
    try:
        guard = build_guard(load_settings())
    except ValidationError:
        return _failure("invalid_arguments", "invalid settings", tool="jev_gate")
    except ValueError as error:
        return _failure("auth", str(error), tool="jev_gate")
    register_log_secrets(guard.redact)
    if repo is not None and "request" in options:
        options["request"] = redact_action(guard.scrub(options["request"]))
    try:
        arguments = _gate_arguments(options, repo, Path.cwd() if shell_cwd is None else shell_cwd, guard)
    except CliError as error:
        return _failure(error.code, str(error), tool="jev_gate", exit_code=error.exit_code)
    return _run_tool("jev_gate", arguments, provider=provider, tests_read_from_file=True, deadline=deadline)


def calibrate_main(argv: Sequence[str]) -> int:
    """Advisory threshold fitting on the caller's labeled rows (ADR-0070); offline, no provider."""
    from jev_judge_mcp import calibrate

    return calibrate.main(argv)


def _gate_options(argv: Sequence[str]) -> dict[str, str]:
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


def _gate_arguments(
    options: Mapping[str, str], repo: Path | None, shell_cwd: Path, guard: SecurityGuard
) -> dict[str, object]:
    try:
        if repo is None:
            repo = repo_root(shell_cwd)
        claims_text = read_state(
            resolve_scoped(str(shell_cwd / options["claims"]), repo),
            guard,
            caps=replace(FILE_JUDGE, file_units_max=GATE.aggregate_evidence_units),
        )
        tests_text = read_state(
            resolve_scoped(str(shell_cwd / options["tests"]), repo),
            guard,
            caps=replace(FILE_JUDGE, file_units_max=GATE.doc_units),
        )
        request, claims = _claims(claims_text, options.get("request"))
        diff, skipped = gate_range.read_range(options["diff"], guard, cwd=repo)
        if skipped:
            raise CliError("secret_file", "range includes a withheld file; review it locally", exit_code=2)
    except ToolError as error:
        raise CliError(error.code, str(error), exit_code=2) from error
    except GitDiffTooLarge as error:
        raise CliError("input_too_large", str(error), exit_code=2) from error
    except GitDiffError as error:
        raise CliError("invalid_arguments", str(error), exit_code=2) from error
    return {
        "request": guard.scrub(request),
        "diff": diff,
        "claims": [guard.scrub(claim) for claim in claims],
        "evidence": [{"id": "cli", "text": "Read by jev-judge-mcp gate from the local repo."}],
        "tests": tests_text,
    }


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
    name: str,
    arguments: Mapping[str, object],
    *,
    provider: JevProvider | None,
    tests_read_from_file: bool = False,
    deadline: float | None = None,
) -> Decision:
    known = {tool.name for tool in TOOLS}
    if name not in known:
        return _failure("invalid_arguments", f"unknown tool {name}", tool=name, exit_code=2)
    try:
        outcome = anyio.run(_execute, name, dict(arguments), provider, tests_read_from_file, deadline)
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
    name: str,
    arguments: dict[str, Any],
    provider: JevProvider | None,
    tests_read_from_file: bool,
    deadline: float | None,
) -> ToolOutcome:
    toolset = Toolset(_runtime(provider, tests_read_from_file=tests_read_from_file), TOOLS)
    try:
        remaining = None if deadline is None else max(0, deadline - time.monotonic())
        with anyio.move_on_after(remaining):
            return await toolset.execute(name, arguments)
        raise ToolError("completion hook judgment deadline expired", code="timeout")
    finally:
        await toolset.aclose()


def _runtime(provider: JevProvider | None, *, tests_read_from_file: bool) -> Runtime:
    settings = load_settings()
    if provider is None:
        return Runtime(settings, tests_read_from_file=tests_read_from_file)
    return Runtime(settings, provider_factory=lambda _settings: provider, tests_read_from_file=tests_read_from_file)


def emit_configuration_failure(tool: str, code: str, message: str) -> int:
    sys.stderr.write(message + "\n")
    return _emit(_failure(code, message, tool=tool))


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


_SHELL_BOUNDARIES = re.compile(r"""'[^']*'|"(?:\\.|[^"\\])*"|\\[\s\S]|(?:^|[ \t])\#[^\n]*|&&|\|\||[&|;\n()]""")
_ASSIGNMENT = re.compile(r"[a-zA-Z_][a-zA-Z_0-9]*=")
_GIT_OPTIONS_WITH_VALUE = frozenset({"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path"})
_GIT_FLAGS = frozenset(
    {"-p", "--paginate", "-P", "--no-pager", "--bare", "--no-replace-objects", "--literal-pathspecs"}
)
_MERGE_FLAGS = frozenset({"--squash", "--merge", "--rebase", "--auto", "--delete-branch", "-s", "-m", "-r", "-d"})


@dataclass(frozen=True, slots=True)
class _CompletionTarget:
    shell_cwd: Path | None
    git_cwd: Path | None
    kind: str
    args: tuple[str, ...]


def _commands(command: str) -> Iterator[tuple[list[str], str]]:
    """Split simple commands at unquoted shell boundaries."""
    start = 0
    for match in _SHELL_BOUNDARIES.finditer(command):
        boundary = match.group()
        if boundary[0] in "'\"\\":
            continue
        yield shlex.split(command[start : match.start()]), boundary
        start = match.end()
    yield shlex.split(command[start:]), ""


def _static_path(cwd: Path | None, word: str) -> Path | None:
    if cwd is None or not word or word == "-" or any(char in word for char in "$`*?[]{}~"):
        return None
    return cwd / word


def _git_target(argv: list[str], cwd: Path | None) -> _CompletionTarget | None:
    git_cwd = cwd
    i = 1
    while i < len(argv):
        word = argv[i]
        if word in _GIT_OPTIONS_WITH_VALUE and i + 1 < len(argv):
            if word == "-C":
                if argv[i + 1]:
                    git_cwd = _static_path(git_cwd, argv[i + 1])
            elif word != "-c" or argv[i + 1].partition("=")[0] != "core.hooksPath":
                git_cwd = None
            i += 2
        elif word in _GIT_FLAGS or (word.startswith("--") and "=" in word):
            if word == "--bare" or word not in _GIT_FLAGS:
                git_cwd = None
            i += 1
        else:
            break
    if argv[i : i + 1] == ["push"]:
        return _CompletionTarget(cwd, git_cwd, "push", tuple(argv[i + 1 :]))
    return None


def _completion_targets(command: str, cwd: Path) -> list[_CompletionTarget]:
    matches: list[_CompletionTarget] = []
    shell_cwd: Path | None = cwd
    previous = ""
    try:
        for words, boundary in _commands(command):
            if previous in ("(", ")", "|", "||", "&"):
                shell_cwd = None
            previous = boundary
            i = 0
            override = False
            while i < len(words):
                word = words[i]
                if word in ("then", "time", "command", "if", "do", "else"):
                    i += 1
                elif _ASSIGNMENT.match(word):
                    override |= word.startswith(("GIT_", "GH_"))
                    i += 1
                else:
                    break
            argv = words[i:]
            if not argv:
                continue
            executable = argv[0] if "/" not in argv[0] else Path(argv[0]).name if argv[0].startswith("/") else ""
            if executable == "cd":
                shell_cwd = (
                    _static_path(shell_cwd, argv[1]) if len(argv) == 2 and boundary in ("&&", ";", "\n") else None
                )
            elif executable == "git":
                target = _git_target(argv, None if override else shell_cwd)
                if target is not None:
                    matches.append(target)
            elif executable == "gh" and argv[1:2] == ["pr"] and argv[2:3] in (["create"], ["merge"]):
                matches.append(_CompletionTarget(shell_cwd, None if override else shell_cwd, argv[2], tuple(argv[3:])))
    except ValueError:
        return []
    return matches


def completion_matches(command: str) -> bool:
    return bool(_completion_targets(command, Path.cwd()))


def _pr_base(repo: Path, target: _CompletionTarget) -> str:
    base = ""
    if target.kind == "merge":
        if any(arg not in _MERGE_FLAGS for arg in target.args):
            raise GitDiffError("the current branch's PR could not be determined")
    else:
        for i, arg in enumerate(target.args):
            name, equals, value = arg.partition("=")
            if name in ("--head", "-H", "--repo", "-R") or arg.startswith(("-H", "-R")):
                raise GitDiffError("the current branch's PR could not be determined")
            if name == "--base" or arg.startswith("-B"):
                if arg.startswith("-B") and name != "-B":
                    base = arg[2:]
                else:
                    base = value if equals else target.args[i + 1] if i + 1 < len(target.args) else ""
                if not base or base.startswith("-"):
                    raise GitDiffError("PR base could not be determined")
    branch = _current_branch(repo)
    if not base:
        result = run(repo, "config", "--get", "branch." + branch + ".gh-merge-base")
        if result.returncode == 0:
            base = result.stdout.decode().strip()
    ref = f"refs/remotes/origin/{base or 'HEAD'}"
    if run(repo, "check-ref-format", ref).returncode:
        raise GitDiffError("PR base could not be determined")
    return ref


def _current_branch(repo: Path) -> str:
    branch = run(repo, "symbolic-ref", "--quiet", "--short", "HEAD")
    if branch.returncode:
        raise GitDiffError("a PR must have a current branch")
    return branch.stdout.decode().strip()


def completion_range(env: Mapping[str, str], repo: Path, target: _CompletionTarget | None = None) -> str | None:
    """Use the push's tracking range or the current branch's PR merge base, entirely offline."""
    if explicit := env.get("JEV_COMPLETION_DIFF"):
        return explicit
    if target is not None and target.kind != "push":
        candidate = f"{_pr_base(repo, target)}...HEAD"
        changes = range_has_changes(repo, candidate)
        if changes is None:
            raise GitDiffError("PR base could not be determined")
        return candidate if changes else None
    for candidate in ("@{upstream}..HEAD", "HEAD~1..HEAD"):
        if (changes := range_has_changes(repo, candidate)) is not None:
            return candidate if changes else None
    empty = run(repo, "hash-object", "-t", "tree", "--stdin")
    if empty.returncode == 0:
        candidate = f"{empty.stdout.decode().strip()}..HEAD"
        if (changes := range_has_changes(repo, candidate)) is not None:
            return candidate if changes else None
    raise GitDiffError("push range could not be determined")


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
    deadline = time.monotonic() + COMPLETION_HARNESS_TIMEOUT_SECONDS - COMPLETION_PROCESS_MARGIN_SECONDS
    body = sys.stdin.read() if text is None else text
    env = os.environ if environ is None else environ
    required = hook_required(env)
    try:
        parsed = decode_json(body)
    except ValueError:
        return fail_open_or_ask(required, "error.code=invalid_arguments\n", "stdin was not hook-event JSON")
    if not is_json_object(parsed):
        return fail_open_or_ask(required, "", "stdin was not hook-event JSON")
    command = command_from_hook_event(parsed)
    origin = parsed.get("cwd")
    cwd = Path(origin) if isinstance(origin, str) and Path(origin).is_absolute() else Path.cwd()
    matches = _completion_targets(command, cwd)
    if not matches:
        return 0
    claims = env.get("JEV_COMPLETION_CLAIMS")
    tests = env.get("JEV_COMPLETION_TESTS")
    if not claims or not tests:
        return fail_open_or_ask(required, "error.code=invalid_arguments\n", "invalid_arguments")
    try:
        if len(matches) != 1 or (target := matches[0]).git_cwd is None or target.shell_cwd is None:
            raise GitDiffError("completion repository could not be resolved")
        repo = repo_root(target.git_cwd)
        diff = completion_range(env, repo, target)
    except (GitDiffError, OSError, ValueError, subprocess.TimeoutExpired, OutputTooLarge):
        return fail_open_or_ask(required, "error.code=invalid_arguments\n", "invalid_arguments")
    if diff is None:
        sys.stderr.write("jev-judge-mcp completion-hook: nothing to gate; the selected range is empty\n")
        return 0
    try:
        decision = run_gate(
            ["--diff", diff, "--claims", claims, "--tests", tests, "--request", command],
            provider=provider,
            deadline=deadline,
            repo=repo,
            shell_cwd=target.shell_cwd,
        )
    except Exception:
        return fail_open_or_ask(required, "error.code=provider\n", "provider")
    if decision.exit_code != 0:
        return _gate_failure(required, decision.error_code)
    if decision.action == "auto":
        return 0
    sys.stdout.write(_completion_ask(decision.action))
    return 0
