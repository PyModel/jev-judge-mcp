"""Shared retry-loop test setup (ADR-0057): offline, deterministic, no real sleeping."""

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
