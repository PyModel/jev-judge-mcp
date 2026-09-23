"""Policy thresholds: the reference defaults (`parity-manifest.json` `defaults`) and the review/gate pair.

Every threshold a tool compares against lives here (ADR-0002); a tool that hardcodes one is a bug.
"""

import math
from dataclasses import dataclass

DEFAULT_AUTO_ACCEPT = 0.8
"""jev_verify, jev_review, jev_gate (`index.ts:1304`, `index.ts:1378`)."""

DEFAULT_REVIEW_AT_CAP = 0.5
"""An omitted review_at becomes `min(0.5, auto_accept)` (`lib.ts:282`)."""

DEFAULT_COMPOSITE_FLOOR = 0.7
"""`lib.ts:238`."""

DEFAULT_CLASSIFY_AUTO_ACCEPT = 0.85
"""jev_classify, jev_compare, jev_extract."""

DEFAULT_MINIMUM_MARGIN = 0.5
"""jev_classify, jev_compare, jev_extract."""

DEFAULT_SCREEN_BLOCK_AT = 0.75
DEFAULT_SCREEN_REVIEW_AT = 0.25

SCREEN_SUBSTANCE_SKIP_BELOW = 0.3
"""Hardcoded in `screenRecommendation` (`lib.ts:82`), not a parameter."""

SCREEN_RELEVANCE_SKIP_BELOW = 0.3
"""Hardcoded in `screenRecommendation` (`lib.ts:84`), not a parameter."""

EXISTS_FOUND_AT = 0.7
"""`existsVerdict` default (`lib.ts:90`)."""

EXISTS_ABSENT_BELOW = 0.35
"""`existsVerdict` default (`lib.ts:90`)."""

THRESHOLD_INVARIANT_MESSAGE = "Thresholds must satisfy 0 <= review_at <= auto_accept <= 1."
"""The reference's thrown text (`lib.ts:273`); the tool reports it verbatim."""


@dataclass(frozen=True, slots=True)
class PolicyThresholds:
    """A review/gate pair that holds: 0 <= review_at <= auto_accept <= 1."""

    auto_accept: float
    review_at: float


@dataclass(frozen=True, slots=True)
class ThresholdInvariant:
    """A pair that does not hold. Not an exception, so a tool cannot catch it as `ValueError`.

    `message` is `THRESHOLD_INVARIANT_MESSAGE`. The reference throws that text (`lib.ts:273`);
    the tool reports the same text verbatim.
    """

    message: str = THRESHOLD_INVARIANT_MESSAGE


def validate_policy_thresholds(auto_accept: float, review_at: float) -> ThresholdInvariant | None:
    """The invariant outcome unless both are finite and 0 <= review_at <= auto_accept <= 1 (`lib.ts:263-275`)."""
    if (
        not math.isfinite(auto_accept)
        or not math.isfinite(review_at)
        or auto_accept < 0
        or auto_accept > 1
        or review_at < 0
        or review_at > 1
        or review_at > auto_accept
    ):
        return ThresholdInvariant()
    return None


def resolve_policy_thresholds(
    auto_accept: float = DEFAULT_AUTO_ACCEPT, review_at: float | None = None
) -> PolicyThresholds | ThresholdInvariant:
    """Fill an omitted review_at so a lone low auto_accept cannot invert the pair, then validate (`lib.ts:278-285`).

    Only `None` means omitted, as JS `??`: an explicit `review_at=0` stands. A broken pair is the
    invariant outcome, not a `ValueError`.
    """
    resolved = review_at if review_at is not None else min(DEFAULT_REVIEW_AT_CAP, auto_accept)
    refused = validate_policy_thresholds(auto_accept, resolved)
    if refused is not None:
        return refused
    return PolicyThresholds(auto_accept=auto_accept, review_at=resolved)
