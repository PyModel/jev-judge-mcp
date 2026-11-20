"""Opt-in command hook: judge one proposed action and deny, ask, or abstain.

``jev-judge-mcp hook gate`` is a short-lived process. It is not an MCP tool.
Stdout is empty when the hook abstains, or one JSON permission decision.
The hook does not log the action or the state.

The judgment is routed by the event's tool name (ADR-0076). A Bash action is judged by two
questions in one provider call: its effect, and whether destruction is the intent. A Write or
Edit is judged by a deterministic repo-containment check computed here — an outside write is
denied before any provider exists — plus one credential-in-content question for what stays
inside. Any other tool name keeps the generic allow/deny question, so a widened matcher still
gets judged.
"""

import os
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from jev_judge_mcp.domain.json import decode_json, is_json_object
from jev_judge_mcp.hook.render import deny_reason, fail_open_or_ask, hook_required, render_decision
from jev_judge_mcp.hook.runtime import HookFailure, judge, prepare
from jev_judge_mcp.policy.guard import WRITE_TOOLS, Ask, decide, questions
from jev_judge_mcp.providers import JevProvider, resolve_provider
from jev_judge_mcp.redaction.action import redact_action
from jev_judge_mcp.redaction.literals import (
    has_credential_field,
    has_credential_literal,
)
from jev_judge_mcp.security import SecurityGuard
from jev_judge_mcp.serialize import stringify_compact
from jev_judge_mcp.text import length
from jev_judge_mcp.tools.base import ToolError
from jev_judge_mcp.tools.file_state import resolve_scoped

HOOK_INPUT_UNITS = 100_000
"""Stdin over this, when ``JEV_HOOK_REQUIRED=1``, asks instead of being judged or staying silent."""

_FAIL_OPEN_STDIN = "jev-judge-mcp hook: stdin was not hook-event JSON\n"
_Containment = Literal["inside", "outside", "unknown"]


@dataclass(frozen=True, slots=True)
class _Plan:
    """The routed judgment for one event: which questions to ask, over which state."""

    tool: str
    containment: _Containment
    credential_matched: bool = False


def run(*, text: str | None, environ: Mapping[str, str] | None, provider: JevProvider | None) -> int:
    body = sys.stdin.read(HOOK_INPUT_UNITS + 1) if text is None else text
    env = os.environ if environ is None else environ
    required = hook_required(env)
    if length(body) > HOOK_INPUT_UNITS:
        return fail_open_or_ask(required, "error.code=input_too_large\n", "input_too_large")
    try:
        parsed = decode_json(body)
    except ValueError:
        return fail_open_or_ask(required, _FAIL_OPEN_STDIN, "stdin was not hook-event JSON")
    if not is_json_object(parsed):
        return fail_open_or_ask(required, _FAIL_OPEN_STDIN, "stdin was not hook-event JSON")
    plan = _plan(parsed)
    if plan.containment == "outside":
        reason = deny_reason("the write targets a path outside the working directory")
        sys.stdout.write(render_decision("deny", reason) + "\n")
        return 0
    if plan.credential_matched:
        reason = deny_reason("the written content contains a credential literal")
        sys.stdout.write(render_decision("deny", reason) + "\n")
        return 0

    prepared = prepare(provider, resolve=resolve_provider)
    if isinstance(prepared, HookFailure):
        return fail_open_or_ask(required, f"jev-judge-mcp hook: fail-open ({prepared.detail})\n", "auth")
    chosen, model, guard = prepared
    state = _event_state(parsed, env.get("JEV_GATE_STATE"), plan, guard)
    judged = judge(chosen, state, questions(plan.tool), model)
    decision = Ask("unreachable") if isinstance(judged, HookFailure) else decide(plan.tool, judged.answers)
    if decision == "allow":
        return 0
    if isinstance(decision, Ask):
        return fail_open_or_ask(True, "", decision.reason)
    reason = deny_reason(decision.reason, f"{decision.measure} {decision.value:.2f}")
    sys.stdout.write(render_decision("deny", reason) + "\n")
    return 0


def _plan(event: dict[str, object]) -> _Plan:
    tool = _text(event.get("tool_name"), "unknown")
    if tool not in WRITE_TOOLS:
        return _Plan(tool, "unknown")
    cwd = event.get("cwd")
    tool_input = event.get("tool_input", {})
    target = tool_input.get("file_path") if is_json_object(tool_input) else None
    containment: _Containment = "unknown"
    if isinstance(cwd, str) and cwd and isinstance(target, str) and target:
        try:
            resolve_scoped(os.path.expanduser(target), Path(cwd))
        except ToolError:
            return _Plan(tool, "outside")
        containment = "inside"
    return _Plan(tool, containment, _has_literal(tool_input))


def _has_literal(value: object, field: str = "") -> bool:
    if isinstance(value, str):
        return has_credential_field(field, value) or has_credential_literal(value)
    if is_json_object(value):
        return any(has_credential_literal(key) or _has_literal(member, key) for key, member in value.items())
    if isinstance(value, list):
        items: list[object] = value  # pyright: ignore[reportUnknownVariableType]
        return any(_has_literal(item) for item in items)
    return False


def _event_state(event: dict[str, object], extra: str | None, plan: _Plan, guard: SecurityGuard) -> str:
    lines = [
        f"An autonomous coding agent is working in: {_text(event.get('cwd'), 'unknown')}",
        f"Permission mode: {_text(event.get('permission_mode'), 'default')}",
    ]
    if extra is not None and extra.strip():
        lines.append(extra.strip())
    lines.append("The agent proposes the tool call below. Judge only whether it is safe to run without human review.")
    parts = [f"Tool: {_text(event.get('tool_name'), 'unknown')}"]
    if plan.containment == "inside":
        parts.append("Repo containment: the target path resolves inside the working directory (computed in code).")
    elif plan.containment == "unknown":
        parts.append("Repo containment: the target path could not be checked against the working directory.")
    description = event.get("description")
    if isinstance(description, str) and description:
        parts.append(f"Description: {description}")
    parts.append(f"Input: {_render_input(event.get('tool_input', {}))}")
    action = guard.scrub("\n".join(parts))
    if plan.tool not in WRITE_TOOLS:
        action = redact_action(action)
    return guard.scrub("\n".join(lines)) + "\n\n--- proposed action ---\n" + action


def _render_input(value: object) -> str:
    if isinstance(value, str):
        return value
    return stringify_compact(value)


def _text(value: object, default: str) -> str:
    if isinstance(value, str):
        return value
    return default
