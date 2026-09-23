"""Actions and the rules that combine them (`lib.ts:60-62`, `lib.ts:126-133`, `lib.ts:288-360`)."""

from collections.abc import Iterable
from typing import Literal

type Action = Literal["auto", "review", "escalate"]
"""`PolicyAction`: auto stands alone, review needs confirmation, escalate must not proceed as is."""

type Decision = Literal["auto", "review"]
"""The two-tier action of verify, classify, compare, and extract: they have no escalate tier."""

type FailClosedTool = Literal["verify", "classify", "compare", "review", "gate", "extract", "decide", "find", "rerank"]
"""Tools whose invalid answer is a `fail_closed` token. jev_screen is not one of them."""

type FailClosedToken = Literal["review", "escalate", "status"]
"""`review` and `escalate` are Actions. `status` means the invalid row has no Action key."""

_FAIL_CLOSED: dict[FailClosedTool, FailClosedToken] = {
    "review": "escalate",
    "gate": "escalate",
    "verify": "review",
    "classify": "review",
    "compare": "review",
    "extract": "status",
    "decide": "status",
    "find": "status",
    "rerank": "status",
}


def fail_closed(tool: FailClosedTool) -> FailClosedToken:
    """The invalid-answer token for `tool`. The tool lays out keys (ADR-0042).

    `status` is not an Action and is not written into the payload. jev_screen stays on
    `screen_fail_closed`.
    """
    return _FAIL_CLOSED[tool]


def verify_action(confidence: float, auto_accept: float) -> Decision:
    """`verifyAction`: auto iff confidence >= auto_accept.

    A valid relation with no confidence never reaches this function: jev_verify maps it to review
    at the call site (`index.ts:216`). A missing or malformed answer is a different check. Its
    token comes from `fail_closed` (ADR-0042).
    """
    return "auto" if confidence >= auto_accept else "review"


def classification_decision(
    top_probability: float, margin: float, auto_accept: float, minimum_margin: float
) -> Decision:
    """`classificationDecision`: auto iff top_probability >= auto_accept and margin >= minimum_margin."""
    return "auto" if top_probability >= auto_accept and margin >= minimum_margin else "review"


def require_complete_context(action: Action, truncated: bool) -> Action:
    """`requireCompleteContext`: truncated input demotes auto to review and leaves stronger actions alone."""
    return "review" if truncated and action == "auto" else action


def worst_action(actions: Iterable[Action]) -> Action:
    """`worstAction`: escalate > review > auto. No actions at all is auto, as in the reference."""
    seen = set(actions)
    if "escalate" in seen:
        return "escalate"
    if "review" in seen:
        return "review"
    return "auto"
