"""Pick an AUTO threshold: maximum coverage subject to the upper error bound <= the error budget.

A row is AUTO when `score >= threshold`, the reference's comparison for every auto_accept. The result
is eval data for a report; it never edits `jev_judge_mcp.policy.thresholds` (a changed frozen default is a
Sanctioned Divergence and needs an ADR).
"""

from collections.abc import Sequence
from dataclasses import dataclass

from jev_judge_mcp.calibration.bounds import Bound, clopper_pearson_upper, upper_error_bound


@dataclass(frozen=True, slots=True)
class OperatingPoint:
    threshold: float
    auto: int
    """Rows at or above the threshold."""
    errors: int
    """AUTO rows whose judgment was wrong."""
    coverage: float
    """auto / all rows."""
    error_upper_bound: float


def select_threshold(
    rows: Sequence[tuple[float, bool]],
    max_error: float,
    bound: Bound = "clopper_pearson",
    confidence: float = 0.95,
) -> OperatingPoint | None:
    """`rows` are `(score, correct)`. Returns the feasible point with the most AUTO rows, else `None`.

    Every distinct score is a candidate threshold. Feasibility is not monotone in the threshold, so all
    candidates are checked; `None` means no threshold meets the budget — never a fallback threshold.
    """
    if not rows:
        return None
    best: OperatingPoint | None = None
    for threshold in sorted({score for score, _ in rows}, reverse=True):
        auto = [correct for score, correct in rows if score >= threshold]
        errors = auto.count(False)
        upper = upper_error_bound(errors, len(auto), bound, confidence)
        if upper <= max_error and (best is None or len(auto) > best.auto):
            best = OperatingPoint(threshold, len(auto), errors, len(auto) / len(rows), upper)
    return best


@dataclass(frozen=True, slots=True)
class Certification:
    """A selected `OperatingPoint` applied to rows it was not chosen on (the `locked_test` split)."""

    auto: int
    """Rows at or above the threshold."""
    errors: int
    """AUTO rows whose judgment was wrong."""
    coverage: float
    """auto / all rows."""
    error_upper_bound: float
    """One-sided 95% Clopper-Pearson upper bound on the error rate, measured on these rows only."""


def certify(point: OperatingPoint, rows: Sequence[tuple[float, bool]], confidence: float = 0.95) -> Certification:
    """Apply `point` — selected on other rows — to held-out `rows` and bound its error rate there.

    Always Clopper-Pearson: a certification claim uses the exact bound. Rows with no evidence bound at
    1.0 (`bounds.py`), so an empty split — or one with no row at or above the threshold — fails the
    gate rather than certifying by default.
    """
    auto = [correct for score, correct in rows if score >= point.threshold]
    errors = auto.count(False)
    return Certification(
        len(auto),
        errors,
        len(auto) / len(rows) if rows else 0.0,
        clopper_pearson_upper(errors, len(auto), confidence),
    )
