"""Borderline cases (within ±0.05 of a threshold, inclusive) are repeated 3-5 times to measure flips."""

import math
from collections.abc import Mapping, Sequence

BORDERLINE_MARGIN = 0.05
MIN_REPEATS = 3
MAX_REPEATS = 5


def is_borderline(score: float, threshold: float, margin: float = BORDERLINE_MARGIN) -> bool:
    """`|score - threshold| <= margin`, with a tolerance so 0.85 vs 0.8 counts despite float error."""
    distance = abs(score - threshold)
    return distance <= margin or math.isclose(distance, margin, rel_tol=0, abs_tol=1e-12)


def flip_rate(repeats: Mapping[str, Sequence[str]]) -> float | None:
    """Share of cases whose repeated decisions disagree. Each case needs 3-5 repeats; `None` if empty."""
    for case, decisions in repeats.items():
        if not MIN_REPEATS <= len(decisions) <= MAX_REPEATS:
            raise ValueError(f"{case}: {len(decisions)} repeats, need {MIN_REPEATS}-{MAX_REPEATS}")
    if not repeats:
        return None
    return sum(1 for decisions in repeats.values() if len(set(decisions)) > 1) / len(repeats)
