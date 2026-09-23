"""The candidate pipeline owns refusal text: each executor result maps to its frozen reason (ADR-0025)."""

from time import monotonic

import pytest

from jev_judge_mcp.extract.candidates import (
    REGEX_POOL_SATURATED_REASON,
    REGEX_TIMEOUT_REASON,
    REGEX_TIMEOUT_S,
    Found,
    Refused,
    find_candidates,
)
from jev_judge_mcp.extract.dialect import Translated, to_units
from jev_judge_mcp.extract.executor import Invalid, Matches, MatchResult, Saturated, Timeout, Unavailable

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class Scripted:
    """An executor that answers `result` and records what it was asked."""

    def __init__(self, result: MatchResult) -> None:
        self.result = result
        self.calls: list[tuple[Translated, str, float]] = []

    async def find(self, pattern: Translated, text: str, *, deadline: float) -> MatchResult:
        self.calls.append((pattern, text, deadline))
        return self.result

    async def aclose(self) -> None:
        return None


@pytest.mark.parametrize(
    ("result", "refused"),
    [
        (Saturated(), Refused("saturated", REGEX_POOL_SATURATED_REASON)),
        (Timeout(), Refused("timeout", REGEX_TIMEOUT_REASON)),
        (Invalid(Unavailable.NO_RESULT), Refused("worker_error", "regex worker exited without a result")),
        (Invalid(Unavailable.NOT_STARTED), Refused("worker_error", "regex worker did not start")),
    ],
    ids=["saturated", "timeout", "no-result", "not-started"],
)
async def test_each_refusal_has_its_own_reason(result: MatchResult, refused: Refused) -> None:
    assert await find_candidates(Scripted(result), "a", "", "a") == refused


async def test_a_rejected_pattern_never_reaches_the_executor() -> None:
    executor = Scripted(Timeout())
    refused = await find_candidates(executor, "(a*)*$", "", "a")
    assert isinstance(refused, Refused)
    assert refused.outcome == "rejected"
    assert "outside the supported subset" in refused.reason
    assert executor.calls == []


async def test_candidates_leave_unit_space_and_the_deadline_is_one_budget() -> None:
    units = to_units("x😀y")
    executor = Scripted(Matches([units[1:]], True, 3))
    before = monotonic()
    assert await find_candidates(executor, "\\S", "i!", units) == Found(["😀y"], True, 3)
    ((pattern, text, deadline),) = executor.calls
    assert text == units
    assert before + REGEX_TIMEOUT_S <= deadline <= monotonic() + REGEX_TIMEOUT_S
    assert pattern.flags != 0  # "i!" normalized to "ig": ignore-case reached the executor
