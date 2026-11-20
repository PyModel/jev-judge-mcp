"""JSON rendering for the command hook. No policy. The bytes are the hook contract."""

import sys
from collections.abc import Mapping

from jev_judge_mcp.domain.json import JsonValue
from jev_judge_mcp.policy.guard import FINAL_BLOCK_NOTICE
from jev_judge_mcp.serialize import stringify_compact


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
