"""JSON rendering for the command hook. No policy. The bytes are the hook contract."""

from jev_judge_mcp.domain.json import JsonValue
from jev_judge_mcp.serialize import stringify_compact


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
