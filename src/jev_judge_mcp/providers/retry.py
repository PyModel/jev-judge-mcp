"""The one retry policy for upstream Jev calls and the math behind it (ADR-0057).

Every provider shares this policy, so there is exactly one definition and one test suite: the
typesafe-sdk transport is constructed with `RetryPolicy(max_retries=0)` (its own default policy is
disabled) and `JevProvider.evaluate` drives the attempts for all four providers. Only transient
transport failures are retried — a connection failure before any response, a per-attempt timeout,
and the status codes in `statuses` — never another 4xx, an envelope or validation error, a provider
configuration error, or a cancelled call.

The module-level `sleep`, `clock` and `uniform` are the loop's dependencies; tests replace them to
stay offline and deterministic.
"""

import math
import random
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from email.utils import parsedate_to_datetime
from typing import Literal

import anyio

type RetryKind = Literal["status", "timeout", "connect"]


async def sleep(seconds: float) -> None:
    """The backoff wait between attempts; cancellation propagates (ADR-0011)."""
    await anyio.sleep(seconds)


def clock() -> float:
    """Monotonic seconds, for the overall retry budget."""
    return time.monotonic()


def uniform() -> float:
    """One random sample in [0, 1), for backoff jitter (not cryptography)."""
    return random.random()  # noqa: S311 - backoff jitter, not cryptography


@dataclass(frozen=True)
class RetryPolicy:
    """The bounded retry shape every provider is driven by (ADR-0057).

    The defaults are the typesafe-sdk `RetryPolicy` defaults (max 2 retries, 0.5 s backoff doubling
    to 5 s, 25% subtractive jitter, 408/429/5xx, `Retry-After` honored) plus two bounds the SDK
    leaves open: a 30 s per-attempt timeout and a 90 s overall budget — three attempts at their
    30 s deadline. Evidence for the numbers is recorded in ADR-0057.
    """

    max_attempts: int = 3
    """Total attempts including the first; `1` disables retries (`NO_RETRIES`)."""

    per_attempt_timeout: float = 30.0
    """Seconds before one attempt is abandoned as a retryable timeout."""

    backoff_initial: float = 0.5
    """First backoff delay in seconds, doubled each attempt up to `backoff_max`; zero disables backoff."""

    backoff_max: float = 5.0
    """Cap on every delay, `Retry-After` hints included; zero disables backoff."""

    backoff_jitter: float = 0.25
    """Fraction of each backoff delay randomly subtracted, between 0 and 1."""

    statuses: frozenset[int] = field(default_factory=lambda: frozenset({408, 429, *range(500, 600)}))
    """HTTP status codes that are retried."""

    budget: float = 90.0
    """Overall seconds one `evaluate` call may spend across attempts and delays."""

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be a positive integer.")
        for name, value in (
            ("per_attempt_timeout", self.per_attempt_timeout),
            ("backoff_initial", self.backoff_initial),
            ("backoff_max", self.backoff_max),
            ("budget", self.budget),
        ):
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be a positive, finite number of seconds.")
        if not 0 <= self.backoff_jitter <= 1:
            raise ValueError("backoff_jitter must be between zero and one.")


DEFAULT_RETRY_POLICY = RetryPolicy()
"""The policy every provider runs unless one is injected."""

NO_RETRIES = RetryPolicy(max_attempts=1)
"""The one injection point that turns retries off: a single, still time-bounded attempt.

Paid runners keep exact call caps with it, and replay-based parity and contract fixtures stay
single-attempt, deterministic and byte-identical (ADR-0057).
"""


@dataclass(frozen=True, slots=True)
class TransientFailure:
    """One attempt's retryable transport failure, classified for the retry loop (ADR-0057)."""

    kind: RetryKind
    status: int | None = None
    """The HTTP status when `kind` is `"status"`."""
    detail: str = ""
    """The failure as the final error should quote it: status and cut body, or the cause."""
    retry_after: float | None = None
    """Server-advertised delay in seconds, when the failure response advertised one."""

    @property
    def describe(self) -> str:
        """Allowlisted log description: the status code or the error class, nothing else."""
        return f"status {self.status}" if self.kind == "status" else self.kind


def backoff_delay(policy: RetryPolicy, attempt: int) -> float:
    """The exponential backoff after failed `attempt` (1-based): doubling, jittered, capped."""
    if policy.backoff_initial == 0 or policy.backoff_max == 0:
        return 0.0
    exponent = attempt - 1
    exponential = (
        policy.backoff_max
        if exponent >= math.log2(policy.backoff_max) - math.log2(policy.backoff_initial)
        else math.ldexp(policy.backoff_initial, exponent)
    )
    return min(exponential, round(exponential * (1 - uniform() * policy.backoff_jitter), 3))


def retry_delay(
    policy: RetryPolicy, attempt: int, elapsed: float, budget: float, retry_after: float | None = None
) -> float | None:
    """The delay before the retry after failed `attempt`, or `None` when retries stop.

    Delays honor a server `Retry-After` hint, capped at `backoff_max`. Retries stop at
    `max_attempts` and when `elapsed + delay` would reach `budget`.
    """
    if attempt >= policy.max_attempts:
        return None
    delay = backoff_delay(policy, attempt) if retry_after is None else min(retry_after, policy.backoff_max)
    if elapsed + delay >= budget:
        return None
    return delay


def retry_after_seconds(headers: Mapping[str, str]) -> float | None:
    """`retry-after-ms` or `Retry-After` (delay seconds or HTTP date) as seconds; `None` otherwise."""
    raw = _header(headers, "retry-after-ms")
    if raw is not None:
        seconds = _finite_seconds(raw, 0.001)
        if seconds is not None:
            return seconds
    raw = _header(headers, "retry-after")
    if raw is not None:
        seconds = _finite_seconds(raw, 1)
        if seconds is not None:
            return seconds
        try:
            when = parsedate_to_datetime(raw.strip())
        except (ValueError, TypeError, OverflowError):
            return None
        return max(0.0, when.timestamp() - time.time())
    return None


def _header(headers: Mapping[str, str], name: str) -> str | None:
    """Case-insensitive single-header lookup for plain mappings and httpx.Headers alike."""
    for key, value in headers.items():
        if key.lower() == name:
            return value
    return None


def _finite_seconds(raw: str, multiplier: float) -> float | None:
    try:
        value = float(raw.strip())
    except ValueError:
        return None
    if not math.isfinite(value) or value < 0:
        return None
    return value * multiplier
