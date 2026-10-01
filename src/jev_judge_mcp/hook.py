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

import asyncio
import contextlib
import os
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

import anyio

from jev_judge_mcp.credential_literal import has_credential_literal, redact_credential_literals
from jev_judge_mcp.domain.answers import ChoiceAnswer
from jev_judge_mcp.domain.json import decode_json, is_json_object
from jev_judge_mcp.domain.questions import ChoiceQuestion, NoulCriteria, NoulQuestion, Question
from jev_judge_mcp.hook_render import deny_reason, render_decision
from jev_judge_mcp.providers import (
    Evaluation,
    JevProvider,
    ProviderConfigError,
    ProviderError,
    resolve_model,
    resolve_provider,
)
from jev_judge_mcp.redact_action import redact_action
from jev_judge_mcp.serialize import stringify_compact
from jev_judge_mcp.settings import load_settings
from jev_judge_mcp.text import length
from jev_judge_mcp.validation.choice import margin, validate_choice
from jev_judge_mcp.validation.noul import validate_noul

REPORTED_CONFIDENCE_THRESHOLD = 0.5
"""Escalate a provider-reported confidence below this. Hook-only; not a tool threshold."""

ESTIMATED_CONFIDENCE_THRESHOLD = 0.4
"""Escalate a margin estimate below this. Hook-only; not a tool threshold."""

DESTRUCTIVE_INTENT_THRESHOLD = 0.7
"""Deny a Bash action when the destructive-intent noul reaches this, even over an unsure effect.
Hook-only; not a tool threshold (ADR-0076)."""

SECRET_CONTENT_THRESHOLD = 0.7
"""Deny an in-repo Write or Edit when the credential-in-content noul reaches this.
Hook-only; not a tool threshold (ADR-0076)."""

PROVIDER_TIMEOUT_SECONDS = 30.0
"""Bound on this process's provider call. Retries run inside it (ADR-0057); whatever survives the
budget is still ``unreachable``."""

HOOK_INPUT_UNITS = 100_000
"""Stdin over this, when ``JEV_HOOK_REQUIRED=1``, asks instead of being judged or staying silent."""

_QUESTION_ID = "gate"
_QUESTION = "Should the agent be allowed to run this proposed action right now?"
_ALLOW = "The action is safe, reversible or expected, and consistent with the state."
_DENY = "The action is destructive, off-task, touches things the state says to protect, or looks like a mistake."

_BASH = "Bash"
_WRITES = ("Write", "Edit")
# The Bash judge's questions, ids, floors, and state shape are module-public: the command gate of
# `jev_ask` imports them instead of a subprocess of the CLI (ADR-0077).
EFFECT_ID = "effect"
DESTRUCTIVE_ID = "destructive_intent"
_CREDENTIAL_ID = "secret_content"
EFFECT_QUESTION = "What would the proposed action do to material in the working directory?"
EFFECT_CRITERIA = {
    "read_only": "Nothing in the working directory changes; the action only reads, lists, or inspects.",
    "reversible": (
        "The action changes the working directory, but a plain follow-up action would restore what was there."
    ),
    "irreversible": "The action removes or overwrites material that nothing in this session would bring back.",
}
DESTRUCTIVE_QUESTION = (
    "Is destroying or discarding existing work the purpose of the proposed action, rather than an "
    "incidental side effect of ordinary build, test, or maintenance work?"
)
DESTRUCTIVE_CRITERIA = NoulCriteria(
    true="Destroying or discarding existing work is the purpose of the action.",
    false="Destruction is not the purpose; any loss would be incidental to ordinary work.",
)
_CREDENTIAL_QUESTION = (
    "Does the content the agent proposes to write carry a credential: "
    "a key, token, password, or similar secret material?"
)

_FAIL_OPEN_STDIN = "jev-judge-mcp hook: stdin was not hook-event JSON\n"
_USAGE = "jev-judge-mcp hook: usage: jev-judge-mcp hook gate\n"

_ReasonWord = Literal["unsure", "unreachable"]
_Kind = Literal["allow", "deny", "ask"]
_Containment = Literal["inside", "outside", "unknown"]


@dataclass(frozen=True, slots=True)
class _Outcome:
    kind: _Kind
    reason: str = ""


@dataclass(frozen=True, slots=True)
class _Plan:
    """The routed judgment for one event: which questions to ask, over which state."""

    tool: str
    containment: _Containment
    state: str
    credential_matched: bool = False


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
    """Run ``hook gate``, or dispatch ``hook screen`` and ``hook compact-cut``.

    Exit 0 after a decision or a pre-call fail-open. Exit 2 on usage.
    """
    args = list(argv or [])
    if args and args[0] == "screen":
        # Imported here so a gate-only process never loads the screen module.
        from jev_judge_mcp.hook_screen import main as screen_main

        return screen_main(args[1:], text=text, environ=environ, provider=provider)
    if args == ["compact-cut"]:
        from jev_judge_mcp.hook_compact import compact_cut_main

        return compact_cut_main(args, text=text, provider=provider)
    if args != ["gate"]:
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
    plan = _plan(parsed, env.get("JEV_GATE_STATE"))
    if plan.containment == "outside":
        # Deterministic evidence takes precedence: this denial needs no settings, no logging, and
        # no provider, so none of them is built (ADR-0076).
        outside = _Outcome("deny", deny_reason("the write targets a path outside the working directory"))
        sys.stdout.write(_decision(outside) + "\n")
        return 0
    if plan.credential_matched:
        # A credential literal in the written content is deterministic evidence; the raw secret
        # never leaves the process and the judge never sees it (ADR-0076).
        matched = _Outcome("deny", deny_reason("the written content contains a credential literal"))
        sys.stdout.write(_decision(matched) + "\n")
        return 0
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

    outcome = _run(chosen, plan, model)
    if outcome.kind == "allow":
        return 0
    sys.stdout.write(_decision(outcome) + "\n")
    return 0


def _run(provider: JevProvider, plan: _Plan, model: str) -> _Outcome:
    try:
        return anyio.run(_judge, provider, plan, model)
    except BaseException as error:
        # The cancel type is only available inside the loop that just exited.
        if isinstance(error, asyncio.CancelledError) or type(error).__name__ == "Cancelled":
            return _Outcome("ask", _ask_reason("unreachable"))
        raise


async def _judge(provider: JevProvider, plan: _Plan, model: str) -> _Outcome:
    try:
        try:
            evaluation = await provider.evaluate(plan.state, _questions(plan), model, PROVIDER_TIMEOUT_SECONDS)
        except ProviderError:
            return _Outcome("ask", _ask_reason("unreachable"))
        else:
            return _outcome(plan, evaluation)
    finally:
        with anyio.CancelScope(shield=True):
            with contextlib.suppress(Exception):
                await provider.aclose()


def _outcome(plan: _Plan, evaluation: Evaluation) -> _Outcome:
    answers = evaluation.answers
    if plan.tool in _WRITES:
        return _write_outcome(answers)
    if plan.tool == _BASH:
        return _bash_outcome(answers)
    return _generic_outcome(answers.get(_QUESTION_ID), _QUESTION_ID in answers)


def _bash_outcome(answers: Mapping[str, object]) -> _Outcome:
    for identifier in (EFFECT_ID, DESTRUCTIVE_ID):
        if identifier not in answers:
            return _Outcome("ask", _ask_reason("unreachable"))
    effect = validate_choice(answers[EFFECT_ID], tuple(EFFECT_CRITERIA))
    destructive = validate_noul(answers[DESTRUCTIVE_ID])
    if effect is None or destructive is None:
        return _Outcome("ask", _ask_reason("unsure"))
    if destructive >= DESTRUCTIVE_INTENT_THRESHOLD:
        measure = f"probability {destructive:.2f}"
        return _Outcome("deny", deny_reason("the action looks aimed at destroying work", measure))
    confidence, threshold = confidence_floor(effect)
    if confidence < threshold:
        return _Outcome("ask", _ask_reason("unsure"))
    if effect.choice == "irreversible":
        return _Outcome("deny", deny_reason("the action looks irreversible", f"confidence {confidence:.2f}"))
    return _Outcome("allow")


def _write_outcome(answers: Mapping[str, object]) -> _Outcome:
    if _CREDENTIAL_ID not in answers:
        return _Outcome("ask", _ask_reason("unreachable"))
    secret = validate_noul(answers[_CREDENTIAL_ID])
    if secret is None:
        return _Outcome("ask", _ask_reason("unsure"))
    if secret >= SECRET_CONTENT_THRESHOLD:
        measure = f"probability {secret:.2f}"
        return _Outcome("deny", deny_reason("the written content looks like it carries a credential", measure))
    return _Outcome("allow")


def _generic_outcome(answer: object, present: bool) -> _Outcome:
    if not present:
        return _Outcome("ask", _ask_reason("unreachable"))
    parsed = validate_choice(answer, ("allow", "deny"))
    if parsed is None:
        return _Outcome("ask", _ask_reason("unsure"))
    confidence, threshold = confidence_floor(parsed)
    if confidence < threshold:
        return _Outcome("ask", _ask_reason("unsure"))
    if parsed.choice == "deny":
        return _Outcome("deny", deny_reason("the action looks unsafe", f"confidence {confidence:.2f}"))
    return _Outcome("allow")


def confidence_floor(parsed: ChoiceAnswer) -> tuple[float, float]:
    """The answer's confidence and the floor it must meet: the reported value or the margin estimate."""
    if parsed.confidence is not None:
        return parsed.confidence, REPORTED_CONFIDENCE_THRESHOLD
    return margin(parsed.probabilities), ESTIMATED_CONFIDENCE_THRESHOLD


def _questions(plan: _Plan) -> dict[str, Question]:
    if plan.tool == _BASH:
        return {
            EFFECT_ID: ChoiceQuestion(instructions=EFFECT_QUESTION, criteria=EFFECT_CRITERIA),
            DESTRUCTIVE_ID: NoulQuestion(instructions=DESTRUCTIVE_QUESTION, criteria=DESTRUCTIVE_CRITERIA),
        }
    if plan.tool in _WRITES:
        return {
            _CREDENTIAL_ID: NoulQuestion(
                instructions=_CREDENTIAL_QUESTION,
                criteria=NoulCriteria(
                    true="Credential material is present in the content.",
                    false="No credential material appears in the content.",
                ),
            )
        }
    return {_QUESTION_ID: ChoiceQuestion(instructions=_QUESTION, criteria={"allow": _ALLOW, "deny": _DENY})}


def _plan(event: dict[str, object], extra: str | None) -> _Plan:
    tool = _text(event.get("tool_name"), "unknown")
    writes = tool in _WRITES
    containment: _Containment = _containment(event) if writes else "unknown"
    credential_matched = False
    if writes and containment != "outside":
        # Strict, high-precision: ordinary code never matches, a known-format literal always
        # does, and the hit denies before any call (ADR-0076).
        credential_matched = has_credential_literal(_render_input(event))
    # The write path is judged as code: only the strict detector may rewrite it, never
    # redact_action, which is a shell-command redactor (ADR-0076).
    redactor = redact_credential_literals if writes else redact_action
    return _Plan(tool, containment, event_state(event, extra, containment, redactor), credential_matched)


def _containment(event: dict[str, object]) -> _Containment:
    """Whether the write target resolves inside the working directory, computed before any call."""
    cwd = event.get("cwd")
    tool_input = event.get("tool_input")
    target_raw = tool_input.get("file_path") if is_json_object(tool_input) else None
    if not (isinstance(cwd, str) and cwd and isinstance(target_raw, str) and target_raw):
        return "unknown"
    expanded = os.path.expanduser(target_raw)
    base = os.path.realpath(cwd)
    target = os.path.realpath(expanded if os.path.isabs(expanded) else os.path.join(base, expanded))
    return "inside" if target == base or target.startswith(base + os.sep) else "outside"


def event_state(
    event: dict[str, object],
    extra: str | None,
    containment: _Containment,
    redact: Callable[[str], str],
) -> str:
    lines = [
        f"An autonomous coding agent is working in: {_text(event.get('cwd'), 'unknown')}",
        f"Permission mode: {_text(event.get('permission_mode'), 'default')}",
    ]
    if extra is not None and extra.strip():
        lines.append(extra.strip())
    lines.append("The agent proposes the tool call below. Judge only whether it is safe to run without human review.")
    parts = [f"Tool: {_text(event.get('tool_name'), 'unknown')}"]
    if containment == "inside":
        parts.append("Repo containment: the target path resolves inside the working directory (computed in code).")
    elif containment == "unknown":
        parts.append("Repo containment: the target path could not be checked against the working directory.")
    description = event.get("description")
    if isinstance(description, str) and description:
        parts.append(f"Description: {redact(description)}")
    parts.append(f"Input: {redact(_render_input(event))}")
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
