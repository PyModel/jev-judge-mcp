"""One-sided upper confidence bounds on an error rate, stdlib only.

`confidence` is one-sided: 0.95 bounds the error rate from above with 95% confidence. A two-sided
95% interval's upper end is the one-sided bound at 0.975.
"""

import math
from statistics import NormalDist
from typing import Literal

Bound = Literal["wilson", "clopper_pearson"]


def _check(errors: int, n: int, confidence: float) -> None:
    if n < 0 or errors < 0 or errors > n:
        raise ValueError(f"need 0 <= errors <= n, got errors={errors}, n={n}")
    if not 0 < confidence < 1:
        raise ValueError(f"confidence must be in (0, 1), got {confidence}")


def wilson_upper(errors: int, n: int, confidence: float = 0.95) -> float:
    """Upper end of the Wilson score interval; 1.0 when there is no evidence (n == 0)."""
    _check(errors, n, confidence)
    if n == 0:
        return 1.0
    z = NormalDist().inv_cdf(confidence)
    p = errors / n
    denominator = 1 + z * z / n
    centre = p + z * z / (2 * n)
    spread = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return min(1.0, (centre + spread) / denominator)


def binomial_cdf(k: int, n: int, p: float) -> float:
    """P(X <= k) for X ~ Binomial(n, p), summed in log space so large n cannot overflow."""
    if p <= 0:
        return 1.0
    if p >= 1:
        return 1.0 if k >= n else 0.0
    log_p, log_q = math.log(p), math.log1p(-p)
    log_n = math.lgamma(n + 1)
    return math.fsum(
        math.exp(log_n - math.lgamma(i + 1) - math.lgamma(n - i + 1) + i * log_p + (n - i) * log_q)
        for i in range(k + 1)
    )


def clopper_pearson_upper(errors: int, n: int, confidence: float = 0.95) -> float:
    """Exact (Clopper-Pearson) upper bound: the p with P(X <= errors; n, p) = 1 - confidence.

    1.0 when errors == n (including n == 0). The CDF falls monotonically in p, so bisection converges.
    """
    _check(errors, n, confidence)
    if errors == n:
        return 1.0
    alpha = 1 - confidence
    low, high = errors / n, 1.0
    for _ in range(200):
        mid = (low + high) / 2
        if binomial_cdf(errors, n, mid) > alpha:
            low = mid
        else:
            high = mid
    return high


def upper_error_bound(errors: int, n: int, bound: Bound, confidence: float = 0.95) -> float:
    return wilson_upper(errors, n, confidence) if bound == "wilson" else clopper_pearson_upper(errors, n, confidence)
