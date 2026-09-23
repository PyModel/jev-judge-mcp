"""jev_screen recommendation (`screenRecommendation`, `lib.ts:70-87`) and its fail-closed fallback."""

from dataclasses import dataclass
from typing import Literal

from jev_judge_mcp.policy.thresholds import SCREEN_RELEVANCE_SKIP_BELOW, SCREEN_SUBSTANCE_SKIP_BELOW
from jev_judge_mcp.serialize import number_to_string, to_fixed

type ScreenAction = Literal["pass", "review", "block", "skip"]


@dataclass(frozen=True, slots=True)
class ScreenRecommendation:
    action: ScreenAction
    reason: str


def screen_recommendation(
    *,
    injection: float,
    block_at: float,
    review_at: float,
    relevance: float | None = None,
    substance: float | None = None,
) -> ScreenRecommendation:
    """Block, then review, on injection; then skip on low substance, then low relevance; else pass.

    `None` means the answer was not asked for. Probabilities print with `toFixed(2)` and thresholds
    with `Number::toString`, as the reference's template literals do.
    """
    if injection >= block_at:
        return ScreenRecommendation(
            "block", f"injection probability {to_fixed(injection)} >= block threshold {number_to_string(block_at)}"
        )
    if injection >= review_at:
        return ScreenRecommendation(
            "review", f"injection probability {to_fixed(injection)} >= review threshold {number_to_string(review_at)}"
        )
    if substance is not None and substance < SCREEN_SUBSTANCE_SKIP_BELOW:
        return ScreenRecommendation("skip", f"little substantive content (substance {to_fixed(substance)})")
    if relevance is not None and relevance < SCREEN_RELEVANCE_SKIP_BELOW:
        return ScreenRecommendation("skip", f"not relevant to the stated purpose (relevance {to_fixed(relevance)})")
    return ScreenRecommendation("pass", "no signals above thresholds")


def screen_fail_closed() -> ScreenRecommendation:
    """A missing or malformed answer is not a clean bill of health: review, never pass."""
    return ScreenRecommendation("review", "missing or malformed answers; cannot screen safely")
