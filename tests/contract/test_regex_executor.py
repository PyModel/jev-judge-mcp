"""The `RegexExecutor` port (ADR-0016): one conformance suite, both adapters.

Every adapter answers the same inputs with the same `MatchResult`. What only a process can do — kill
a runaway match, bound admission, recover after a kill — is pinned in `test_extract.py`.
"""

from collections.abc import AsyncIterator, Callable
from dataclasses import replace
from time import monotonic

import pytest

from jev_judge_mcp.extract.dialect import Translated, to_units, translate
from jev_judge_mcp.extract.executor import InProcessRegexExecutor, Invalid, Matches, RegexExecutor, Timeout, Unavailable
from jev_judge_mcp.extract.worker import ProcessRegexExecutor
from jev_judge_mcp.limits import EXTRACT, ExtractCaps

pytestmark = pytest.mark.anyio

ADAPTERS: dict[str, Callable[[ExtractCaps], RegexExecutor]] = {
    "process": lambda caps: ProcessRegexExecutor(size=1, caps=caps),
    "in-process": lambda caps: InProcessRegexExecutor(caps=caps),
}


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(params=list(ADAPTERS))
async def executor(request: pytest.FixtureRequest) -> AsyncIterator[RegexExecutor]:
    adapter = ADAPTERS[request.param](EXTRACT)
    try:
        yield adapter
    finally:
        await adapter.aclose()


async def find(executor: RegexExecutor, pattern: str, text: str, flags: str = "g") -> object:
    return await executor.find(translate(pattern, flags), to_units(text), deadline=monotonic() + 5)


async def test_matches_drop_empties_and_duplicates_in_order(executor: RegexExecutor) -> None:
    assert await find(executor, "b*", "abba b bb") == Matches(["bb", "b"], False, 0)


async def test_matches_stay_in_unit_space(executor: RegexExecutor) -> None:
    assert await find(executor, "\\ude00.", "x😀y") == Matches(["\ude00y"], False, 0)


async def test_matches_stop_at_the_candidate_cap(executor: RegexExecutor) -> None:
    cap = EXTRACT.candidates_per_field
    text = " ".join(f"n{i}" for i in range(cap + 5))
    assert await find(executor, "n\\d+", text) == Matches([f"n{i}" for i in range(cap)], True, 0)


async def test_matches_over_the_unit_cap_are_counted_not_kept(executor: RegexExecutor) -> None:
    text = "a" * (EXTRACT.candidate_units + 1) + " b " + "c" * (EXTRACT.candidate_units + 1)
    assert await find(executor, "\\S+", text) == Matches(["b"], False, 2)


@pytest.mark.parametrize("adapter", list(ADAPTERS))
async def test_the_caps_reach_the_matcher(adapter: str) -> None:
    """Both caps come from the executor's `ExtractCaps` (`limits.EXTRACT` by default), never a copy."""
    executor = ADAPTERS[adapter](replace(EXTRACT, candidates_per_field=2, candidate_units=3))
    try:
        assert await find(executor, "\\S+", "aaaa b cc d e") == Matches(["b", "cc"], True, 1)
    finally:
        await executor.aclose()


async def test_a_spent_deadline_is_a_timeout(executor: RegexExecutor) -> None:
    result = await executor.find(translate("a", "g"), "a", deadline=monotonic() - 0.001)
    assert result == Timeout()


async def test_a_pattern_that_does_not_compile_is_invalid(executor: RegexExecutor) -> None:
    result = await executor.find(Translated("(", 0), "a", deadline=monotonic() + 5)
    assert result == Invalid(Unavailable.NO_RESULT)


async def test_the_executor_serves_after_an_invalid_pattern(executor: RegexExecutor) -> None:
    await executor.find(Translated("(", 0), "a", deadline=monotonic() + 5)
    assert await find(executor, "a+", "aa") == Matches(["aa"], False, 0)
