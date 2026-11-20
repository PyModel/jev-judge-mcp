"""The one retry policy for upstream Jev calls and the math behind it (ADR-0057).

`JevProvider.evaluate` drives the attempts for all four providers.
Only failures known to precede delivery and rate-limit rejections qualify for replay.

The module-level `sleep`, `clock` and `uniform` are the loop's dependencies; tests replace them to
stay offline and deterministic.
"""

import math
import random
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC
from email.utils import parsedate_to_datetime
from typing import Literal

import anyio

type RetryKind = Literal["status", "timeout", "connect"]

MIN_USEFUL_ATTEMPT = 1.0
"""Seconds reserved for a retry, allowing headroom over Jev's measured 70 to 500 ms latency."""


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

    The defaults allow three attempts, 30 s per attempt and a 90 s overall budget.
    Backoff starts at 0.5 s and doubles to 5 s with 25% subtractive jitter.
    A server's `Retry-After` sets a minimum delay.
    """

    max_attempts: int = 3
    """Total attempts including the first; `1` disables retries (`NO_RETRIES`)."""

    per_attempt_timeout: float = 30.0
    """Seconds before one attempt is abandoned."""

    backoff_initial: float = 0.5
    """First backoff delay in seconds, doubled each attempt up to `backoff_max`."""

    backoff_max: float = 5.0
    """Cap on generated backoff. A server minimum is never shortened."""

    backoff_jitter: float = 0.25
    """Fraction of each backoff delay randomly subtracted, between 0 and 1."""

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
    exponent = attempt - 1
    exponential = (
        policy.backoff_max
        if exponent >= math.log2(policy.backoff_max) - math.log2(policy.backoff_initial)
        else math.ldexp(policy.backoff_initial, exponent)
    )
    jittered = exponential * (1 - uniform() * policy.backoff_jitter)
    return min(exponential, round(jittered, 3) or jittered or exponential)


def retry_delay(
    policy: RetryPolicy, attempt: int, elapsed: float, budget: float, retry_after: float | None = None
) -> float | None:
    """The delay before the retry after failed `attempt`, or `None` when retries stop.

    Delays honor a server `Retry-After` minimum. A retry needs both an attempt slot and enough time
    after the delay to make a useful attempt.
    """
    if attempt >= policy.max_attempts:
        return None
    delay = max(backoff_delay(policy, attempt), retry_after or 0.0)
    if budget - elapsed - delay < MIN_USEFUL_ATTEMPT:
        return None
    return delay


def retry_after_seconds(headers: Mapping[str, str]) -> float | None:
    """`retry-after-ms` or `Retry-After` (delay seconds or HTTP date) as seconds; `None` otherwise."""
    raw = header_value(headers, "retry-after-ms")
    if raw is not None:
        seconds = _finite_seconds(raw, 0.001)
        if seconds is not None:
            return seconds
    raw = header_value(headers, "retry-after")
    if raw is not None:
        seconds = _finite_seconds(raw, 1)
        if seconds is not None:
            return seconds
        try:
            when = parsedate_to_datetime(raw.strip())
        except (ValueError, TypeError, OverflowError):
            return None
        if when.tzinfo is None:
            # A `-0000` zone parses naive; an HTTP-date is UTC, never the host's local time.
            when = when.replace(tzinfo=UTC)
        return max(0.0, when.timestamp() - time.time())
    return None


def header_value(headers: Mapping[str, str], name: str) -> str | None:
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
