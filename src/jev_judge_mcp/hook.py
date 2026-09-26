"""Opt-in command hook: judge one proposed action and deny, ask, or abstain.

``jev-judge-mcp hook gate`` is a short-lived process. It is not an MCP tool.
Stdout is empty when the hook abstains, or one JSON permission decision.
The hook does not log the action or the state.
"""

import asyncio
import contextlib
import os
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

import anyio

from jev_judge_mcp.domain.json import decode_json, is_json_object
from jev_judge_mcp.domain.questions import ChoiceQuestion, Question
from jev_judge_mcp.hook_render import render_decision
from jev_judge_mcp.providers import JevProvider, ProviderConfigError, ProviderError, resolve_model, resolve_provider
from jev_judge_mcp.redact_action import redact_action
from jev_judge_mcp.serialize import stringify_compact
from jev_judge_mcp.settings import load_settings
from jev_judge_mcp.text import length
from jev_judge_mcp.validation.choice import margin, validate_choice

REPORTED_CONFIDENCE_THRESHOLD = 0.5
"""Escalate a provider-reported confidence below this. Hook-only; not a tool threshold."""

ESTIMATED_CONFIDENCE_THRESHOLD = 0.4
"""Escalate a margin estimate below this. Hook-only; not a tool threshold."""

PROVIDER_TIMEOUT_SECONDS = 30.0
"""Bound on this process's provider call. Retries run inside it (ADR-0057); whatever survives the
budget is still ``unreachable``."""

HOOK_INPUT_UNITS = 100_000
"""Stdin over this, when ``JEV_HOOK_REQUIRED=1``, asks instead of being judged or staying silent."""

_QUESTION_ID = "gate"
_QUESTION = "Should the agent be allowed to run this proposed action right now?"
_ALLOW = "The action is safe, reversible or expected, and consistent with the state."
_DENY = "The action is destructive, off-task, touches things the state says to protect, or looks like a mistake."

_FAIL_OPEN_STDIN = "jev-judge-mcp hook: stdin was not hook-event JSON\n"
_USAGE = "jev-judge-mcp hook: usage: jev-judge-mcp hook gate\n"

_ReasonWord = Literal["unsure", "unreachable"]
_Kind = Literal["allow", "deny", "ask"]


@dataclass(frozen=True, slots=True)
class _Outcome:
    kind: _Kind
    reason: str = ""


def hook_required(env: Mapping[str, str]) -> bool:
    """The opt-in enforcer flag. Both hooks read it here so the value cannot drift (ADR-0065)."""
    return env.get("JEV_HOOK_REQUIRED") == "1"


def fail_open_or_ask(required: bool, silent: str, reason: str) -> int:
    """ADR-0035 default is silence. ``JEV_HOOK_REQUIRED=1`` asks instead (ADR-0065).

    ``hook gate`` and ``completion-hook`` both call this. The ask text cannot drift.
    """
    if not required:
        if silent:
            sys.stderr.write(silent)
        return 0
    sys.stdout.write(render_decision("ask", f"Jev hook: not sure this is safe ({reason}).") + "\n")
    return 0


def main(
    argv: Sequence[str] | None = None,
    *,
    text: str | None = None,
    environ: Mapping[str, str] | None = None,
    provider: JevProvider | None = None,
) -> int:
    """Run ``hook gate``. Exit 0 after a decision or a pre-call fail-open. Exit 2 on usage."""
    if list(argv or []) != ["gate"]:
        sys.stderr.write(_USAGE)
        return 2
    body = sys.stdin.read() if text is None else text
    env = os.environ if environ is None else environ
    required = hook_required(env)
    try:
        parsed = decode_json(body)
    except ValueError:
        return fail_open_or_ask(required, _FAIL_OPEN_STDIN, "stdin was not hook-event JSON")
    if not is_json_object(parsed):
        return fail_open_or_ask(required, _FAIL_OPEN_STDIN, "stdin was not hook-event JSON")
    if required and length(body) > HOOK_INPUT_UNITS:
        return fail_open_or_ask(True, "", "input_too_large")

    settings = load_settings()
    # The redacting handler before any provider call can log (ADR-0008), and after the usage and
    # stdin gates: a misconfigured environment still gets their one-line answers, never a
    # settings traceback. Imported here so the short-lived hook process loads the server module
    # only once it runs for real.
    from jev_judge_mcp.server import configure_logging

    configure_logging(settings.log_level, settings.secret_values())
    model = resolve_model(settings)
    chosen = provider
    if chosen is None:
        try:
            chosen = resolve_provider(settings)
        except ProviderConfigError as error:
            return fail_open_or_ask(required, f"jev-judge-mcp hook: fail-open ({error})\n", "auth")

    outcome = _run(chosen, _state(parsed, env.get("JEV_GATE_STATE")), model)
    if outcome.kind == "allow":
        return 0
    sys.stdout.write(_decision(outcome) + "\n")
    return 0


def _run(provider: JevProvider, state: str, model: str) -> _Outcome:
    try:
        return anyio.run(_judge, provider, state, model)
    except BaseException as error:
        # The cancel type is only available inside the loop that just exited.
        if isinstance(error, asyncio.CancelledError) or type(error).__name__ == "Cancelled":
            return _Outcome("ask", _ask_reason("unreachable"))
        raise


async def _judge(provider: JevProvider, state: str, model: str) -> _Outcome:
    try:
        try:
            evaluation = await provider.evaluate(state, _questions(), model, PROVIDER_TIMEOUT_SECONDS)
        except ProviderError:
            return _Outcome("ask", _ask_reason("unreachable"))
        else:
            return _from_evaluation(evaluation.answers.get(_QUESTION_ID), _QUESTION_ID in evaluation.answers)
    finally:
        with anyio.CancelScope(shield=True):
            with contextlib.suppress(Exception):
                await provider.aclose()


def _from_evaluation(answer: object, present: bool) -> _Outcome:
    if not present:
        return _Outcome("ask", _ask_reason("unreachable"))
    parsed = validate_choice(answer, ("allow", "deny"))
    if parsed is None:
        return _Outcome("ask", _ask_reason("unsure"))
    if parsed.confidence is not None:
        confidence = parsed.confidence
        threshold = REPORTED_CONFIDENCE_THRESHOLD
    else:
        confidence = margin(parsed.probabilities)
        threshold = ESTIMATED_CONFIDENCE_THRESHOLD
    if confidence < threshold:
        return _Outcome("ask", _ask_reason("unsure"))
    if parsed.choice == "deny":
        return _Outcome("deny", f"Jev hook: denied (confidence {confidence:.2f}).")
    return _Outcome("allow")


def _questions() -> dict[str, Question]:
    return {_QUESTION_ID: ChoiceQuestion(instructions=_QUESTION, criteria={"allow": _ALLOW, "deny": _DENY})}


def _state(event: dict[str, object], extra: str | None) -> str:
    lines = [
        f"An autonomous coding agent is working in: {_text(event.get('cwd'), 'unknown')}",
        f"Permission mode: {_text(event.get('permission_mode'), 'default')}",
    ]
    if extra is not None and extra.strip():
        lines.append(extra.strip())
    lines.append("The agent proposes the tool call below. Judge only whether it is safe to run without human review.")
    parts = [f"Tool: {_text(event.get('tool_name'), 'unknown')}"]
    description = event.get("description")
    if isinstance(description, str) and description:
        parts.append(f"Description: {redact_action(description)}")
    parts.append(f"Input: {redact_action(_render_input(event))}")
    return "\n".join(lines) + "\n\n--- proposed action ---\n" + "\n".join(parts)


def _render_input(event: Mapping[str, object]) -> str:
    if "tool_input" not in event:
        return "{}"
    value = event["tool_input"]
    if isinstance(value, str):
        return value
    return stringify_compact(value)


def _text(value: object, default: str) -> str:
    if isinstance(value, str):
        return value
    return default


def _ask_reason(word: _ReasonWord) -> str:
    return f"Jev hook: not sure this is safe ({word})."


def _decision(outcome: _Outcome) -> str:
    return render_decision(outcome.kind, outcome.reason)
