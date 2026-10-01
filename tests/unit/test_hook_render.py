"""The hook's rendered bytes: every deny ends with the final-block notice, and allow is not a kind."""

import json

from jev_judge_mcp.hook_render import FINAL_BLOCK_NOTICE, deny_reason, render_decision


def test_every_deny_reason_ends_with_the_final_block_notice() -> None:
    """The notice is operator-facing contract, pinned verbatim: the block is final, not a hint."""
    assert FINAL_BLOCK_NOTICE == (
        "This block is final: do not retry the action, split it into smaller calls, or route it through another tool."
    )
    with_measure = deny_reason("the action looks irreversible", "confidence 0.91")
    deterministic = deny_reason("the write targets a path outside the working directory")
    assert with_measure == f"Jev hook: denied: the action looks irreversible (confidence 0.91). {FINAL_BLOCK_NOTICE}"
    outside = f"Jev hook: denied: the write targets a path outside the working directory. {FINAL_BLOCK_NOTICE}"
    assert deterministic == outside
    assert with_measure.endswith(FINAL_BLOCK_NOTICE)
    assert deterministic.endswith(FINAL_BLOCK_NOTICE)


def test_render_decision_never_renders_allow() -> None:
    """The wire carries deny or ask only (ADR-0035); any other kind renders as ask."""
    deny = json.loads(render_decision("deny", "r"))
    assert deny["hookSpecificOutput"]["permissionDecision"] == "deny"
    ask = json.loads(render_decision("ask", "r"))
    fallback = json.loads(render_decision("allow", "r"))
    assert ask["hookSpecificOutput"]["permissionDecision"] == "ask"
    assert fallback["hookSpecificOutput"]["permissionDecision"] == "ask"
    assert "allow" not in render_decision("allow", "r")
