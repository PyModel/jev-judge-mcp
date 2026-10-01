"""JSON rendering for the command hook. No policy. The bytes are the hook contract."""

from jev_judge_mcp.domain.json import JsonValue
from jev_judge_mcp.serialize import stringify_compact

FINAL_BLOCK_NOTICE = (
    "This block is final: do not retry the action, split it into smaller calls, or route it through another tool."
)
"""Every deny reason ends with this. The harness shows the reason to the agent; the notice tells
it the decision is not a suggestion (ADR-0076). Pinned verbatim by the render-boundary test."""


def deny_reason(failure: str, measure: str = "") -> str:
    """A deny reason names the failure and its confidence, then the final-block notice.

    ``measure`` is the confidence or probability behind the call, or empty for a deterministic
    denial that no model made.
    """
    tail = f" ({measure})" if measure else ""
    return f"Jev hook: denied: {failure}{tail}. {FINAL_BLOCK_NOTICE}"


def render_decision(kind: str, reason: str) -> str:
    """One PreToolUse decision. ``kind`` is ``deny`` or ``ask``. Never ``allow``."""
    decision: str = "deny" if kind == "deny" else "ask"
    payload: dict[str, JsonValue] = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": decision,
            "permissionDecisionReason": reason,
        }
    }
    return stringify_compact(payload)


def render_annotation(context: str) -> str:
    """One PostToolUse annotation. Additional context only: never a block, never a rewrite."""
    payload: dict[str, JsonValue] = {
        "hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "additionalContext": context,
        }
    }
    return stringify_compact(payload)
