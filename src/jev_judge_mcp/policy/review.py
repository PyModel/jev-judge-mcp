"""Patch-review policy: the weighted composite and the review action (`lib.ts:245-337`, `index.ts:1254-1257`)."""

import math
from collections.abc import Iterable

from jev_judge_mcp.policy.actions import Action

REVIEW_WEIGHTS: dict[str, float] = {
    "correctness": 0.4,
    "spec_match": 0.3,
    "test_gap": 0.15,
    "blast_radius": 0.15,
}
"""`REVIEW_WEIGHTS` in its reference key order, which is also the serialized order of `weights`."""


def _clamp(value: float, low: float, high: float) -> float:
    """`Math.min(high, Math.max(low, value))`: NaN stays NaN, where Python's `min`/`max` would drop it."""
    if math.isnan(value):
        return value
    return min(high, max(low, value))


def review_composite(correctness: float, spec_match: float, test_gap: float, blast_radius: float) -> float:
    """`reviewComposite`: weighted 0..1 composite of 0..2 rubric scores, test gap and blast radius inverted.

    Inputs are clamped to [0, 2] first. The sum is written out left to right so each float64 add
    happens in the reference's order.
    """
    c = _clamp(_clamp(correctness, 0.0, 2.0) / 2, 0.0, 1.0)
    s = _clamp(_clamp(spec_match, 0.0, 2.0) / 2, 0.0, 1.0)
    t = _clamp(1 - _clamp(test_gap, 0.0, 2.0) / 2, 0.0, 1.0)
    b = _clamp(1 - _clamp(blast_radius, 0.0, 2.0) / 2, 0.0, 1.0)
    return (
        REVIEW_WEIGHTS["correctness"] * c
        + REVIEW_WEIGHTS["spec_match"] * s
        + REVIEW_WEIGHTS["test_gap"] * t
        + REVIEW_WEIGHTS["blast_radius"] * b
    )


def min_confidence(confidences: Iterable[float | None]) -> float | None:
    """The lowest rubric confidence, or `None` when any is unknown (`index.ts:1256-1257`).

    Unknown on one rubric is unknown overall: it must not become a number that could satisfy a
    threshold. No confidences at all is `inf`, as `Math.min()` is.
    """
    values = list(confidences)
    if any(value is None for value in values):
        return None
    return min((value for value in values if value is not None), default=math.inf)


def review_action(
    *,
    composite: float,
    safe_to_apply: float,
    min_confidence: float | None,
    auto_accept: float,
    review_at: float,
    composite_floor: float,
) -> Action:
    """`reviewAction` (`lib.ts:319-337`).

    Escalate when min_confidence is unknown or below review_at, or safe_to_apply is below review_at.
    Auto when safe_to_apply and min_confidence reach auto_accept and composite reaches
    composite_floor. Otherwise review. Truncation is applied after, by `require_complete_context`.
    """
    if min_confidence is None or min_confidence < review_at or safe_to_apply < review_at:
        return "escalate"
    if safe_to_apply >= auto_accept and composite >= composite_floor and min_confidence >= auto_accept:
        return "auto"
    return "review"
