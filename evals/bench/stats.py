"""Exact paired tests on the binomial in `evals.calibration.bounds`, stdlib only.

Both tests reduce to a two-sided exact binomial test at p = 1/2 on the untied pairs: McNemar on the
discordant items (B right and A wrong vs A right and B wrong), the sign test on per-item wall-time
differences (ties dropped). The two-sided p is twice the smaller tail, capped at 1; no pairs is p = 1.
"""

from evals.calibration.bounds import binomial_cdf


def two_sided_binomial_p(successes: int, failures: int) -> float:
    if successes < 0 or failures < 0:
        raise ValueError(f"need non-negative counts, got {successes}, {failures}")
    n = successes + failures
    if n == 0:
        return 1.0
    return min(1.0, 2 * binomial_cdf(min(successes, failures), n, 0.5))


def mcnemar_exact(b_only: int, a_only: int) -> float:
    """Items B got right and A wrong, against items A got right and B wrong."""
    return two_sided_binomial_p(b_only, a_only)


def sign_test(differences: list[float]) -> float:
    """Signs of the per-item differences; zero differences are ties and drop out."""
    return two_sided_binomial_p(sum(d > 0 for d in differences), sum(d < 0 for d in differences))


def percentile(values: list[float], pct: float) -> float:
    """Linear percentile on the sorted sample. One value is every percentile; an empty sample errors."""
    if not values:
        raise ValueError("percentile of an empty sample")
    if not 0 <= pct <= 100:
        raise ValueError(f"percentile must be between 0 and 100, got {pct}")
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * pct / 100
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)
