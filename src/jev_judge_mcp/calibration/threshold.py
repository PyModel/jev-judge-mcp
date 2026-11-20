"""Pick an AUTO threshold: maximum coverage subject to the upper error bound <= the error budget.

A row is AUTO when `score >= threshold`, the reference's comparison for every auto_accept. The result
is eval data for a report; it never edits `jev_judge_mcp.policy.thresholds` (a changed frozen default is a
Sanctioned Divergence and needs an ADR).
"""

from collections.abc import Sequence
from dataclasses import dataclass
from itertools import groupby
from math import isnan
from operator import itemgetter
from typing import Final

from jev_judge_mcp.calibration.bounds import Bound, clopper_pearson_upper, upper_error_bound

DEFAULT_CONFIDENCE: Final = 0.95
"""The one-sided confidence of every bound here: selection, certification, and the row-count
estimate `calibrate` prints when no threshold is feasible."""


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
    confidence: float = DEFAULT_CONFIDENCE,
) -> OperatingPoint | None:
    """`rows` are `(score, correct)`. Returns the feasible point with the most AUTO rows, else `None`.

    Sweep tied scores in ascending order, removing a group after testing its threshold. The first
    feasible threshold has the largest AUTO population even when feasibility is non-monotone.
    """
    if not rows:
        return None
    ordered = sorted((row for row in rows if not isnan(row[0])), key=itemgetter(0))
    auto = len(ordered)
    errors = sum(not correct for _, correct in ordered)
    if not ordered:
        upper = upper_error_bound(0, 0, bound, confidence)
        return OperatingPoint(rows[0][0], 0, 0, 0.0, upper) if upper <= max_error else None
    for threshold, tied in groupby(ordered, key=itemgetter(0)):
        # The exact interval starts at the observed error rate.
        if bound == "wilson" or not 0 < confidence < 1 or errors / auto <= max_error:
            upper = upper_error_bound(errors, auto, bound, confidence)
            if upper <= max_error:
                return OperatingPoint(threshold, auto, errors, auto / len(rows), upper)
        for _, correct in tied:
            auto -= 1
            errors -= not correct
    return None


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


def certify(
    point: OperatingPoint, rows: Sequence[tuple[float, bool]], confidence: float = DEFAULT_CONFIDENCE
) -> Certification:
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
