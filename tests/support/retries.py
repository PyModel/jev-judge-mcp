"""Shared retry-loop test setup (ADR-0057): offline, deterministic, no real sleeping."""

from dataclasses import dataclass, field

import pytest

from jev_judge_mcp.providers import retry as retry_timing


@pytest.fixture
def fast_retries(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """No real sleeping, full (unjittered) backoff, every delay recorded in order."""
    delays: list[float] = []

    async def instant(seconds: float) -> None:
        delays.append(seconds)

    monkeypatch.setattr(retry_timing, "sleep", instant)
    monkeypatch.setattr(retry_timing, "uniform", lambda: 0.0)
    return delays


@dataclass
class ControlledClock:
    """The retry loop's clock on a test-driven timeline (ADR-0057): time moves only when the
    loop sleeps or the test advances it, never with the runner's wall clock.

    `delays` records every backoff the loop asked for, in order.
    """

    now: float = 0.0
    delays: list[float] = field(default_factory=list[float])

    def read(self) -> float:
        """The injected `clock`: the current position on the controlled timeline."""
        return self.now

    async def wait(self, seconds: float) -> None:
        """The injected `sleep`: record the delay and advance the timeline by it."""
        self.delays.append(seconds)
        self.now += seconds

    def advance(self, seconds: float) -> None:
        """Spend `seconds` of timeline on work the loop does not sleep through."""
        self.now += seconds


@pytest.fixture
def controlled_clock(monkeypatch: pytest.MonkeyPatch) -> ControlledClock:
    """Clock, sleep and jitter on a controlled timeline: retry decisions read the timeline, so
    no outcome depends on runner speed. A test advances the clock by the time work consumes."""
    clock = ControlledClock()
    monkeypatch.setattr(retry_timing, "clock", clock.read)
    monkeypatch.setattr(retry_timing, "sleep", clock.wait)
    monkeypatch.setattr(retry_timing, "uniform", lambda: 0.0)
    return clock
