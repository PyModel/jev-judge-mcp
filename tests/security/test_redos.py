"""A ReDoS corpus against jev_extract (ROADMAP P6, ADR-0004, ADR-0016, ADR-0018).

Every catastrophic pattern the subset accepts returns `invalid_pattern` with the frozen timeout reason
inside the one existing deadline, all at once, while other calls keep being served; patterns the
subset refuses fail fast with their rejection reason. Matching stays on stdlib `re`: `regex` would
finish some of these at once and turn the reference's `invalid_pattern` into a result.
"""

import json
import time
from typing import Any

import anyio
import pytest

from jev_judge_mcp.extract.candidates import REGEX_TIMEOUT_REASON, REGEX_TIMEOUT_S
from jev_judge_mcp.extract.dialect import translate
from jev_judge_mcp.extract.worker import ProcessRegexExecutor
from jev_judge_mcp.settings import Settings
from jev_judge_mcp.tools import TOOLS, Runtime, Toolset
from tests.support.jev import FakeProvider, text_of

pytestmark = pytest.mark.anyio

CATASTROPHIC = [
    ("(a+)+$", "a" * 30 + "!"),
    ("(a|aa)+$", "a" * 40 + "!"),
    ("(a|a)+$", "a" * 30 + "!"),
    ("((a+)+)+$", "a" * 28 + "!"),
    ("^(a+)+b", "a" * 30),
    ("(x+x+)+y", "x" * 30),
    ("([a-z]+)*$", "a" * 30 + "1"),
    ("(\\d+)*[a-z]$", "1" * 30 + "!"),
    ("(\\w+\\s?)*$", "word " * 12 + "!"),
    ("^([a-zA-Z0-9]+\\s?)*$", "abc " * 20 + "!"),
]
"""Nested and overlapping quantifiers with a forced failure at the end: exponential in `re`, as in V8."""

REFUSED = [
    ("(a*)*$", "a" * 30 + "!"),
    ("(\\s*\\S*)*!$", " a" * 20),
]
"""Star-of-empty groups: outside the subset (ADR-0018), refused before any worker runs."""

COMPILE_HEAVY = [r"\W" * 249 + ending for ending in (r"\D", r"\S", r"\.")]

SLACK_S = 0.9
"""Scheduling headroom over the 1 s deadline for ten concurrent kills on a loaded CI host."""

BENIGN: dict[str, Any] = {
    "document": "Build ABC-123 passed.",
    "fields": [{"id": "build", "pattern": "[A-Z]{3}-\\d+", "description": "The build id."}],
}
ANSWERS = {"f0": {"choice": "c0", "probabilities": {"c0": 0.99, "none_of_them": 0.01}, "confidence": 0.99}}


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def extract(pattern: str, document: str) -> dict[str, Any]:
    return {"document": document, "fields": [{"id": "f", "pattern": pattern, "description": "Pathological."}]}


async def test_max_length_ignore_case_translation_does_not_stall_the_loop() -> None:
    gaps: list[float] = []

    async def probe(*, task_status: anyio.abc.TaskStatus[None]) -> None:
        began = time.monotonic()
        task_status.started()
        await anyio.sleep(0)
        gaps.append(time.monotonic() - began)

    async with anyio.create_task_group() as tg:
        await tg.start(probe)
        translate(r"\W" * 250, "gi")
    (gap,) = gaps
    assert gap < 0.05


async def test_corpus_times_out_inside_the_deadline_while_other_calls_are_served() -> None:
    # Keep slots available for benign calls even when both classes of patterns arrive together.
    executor = ProcessRegexExecutor(size=len(CATASTROPHIC) + len(COMPILE_HEAVY) + 2)
    toolset = Toolset(Runtime(Settings(), lambda _: FakeProvider(ANSWERS), executor), TOOLS)
    # The production startup path (serve -> Toolset.awarm), not a hand-warmed pool: this is the
    # warm the served pool actually gets (ADR-0058).
    await toolset.awarm()
    slow: dict[str, tuple[float, str]] = {}
    compiled: dict[str, object] = {}
    served: list[float] = []
    started = time.monotonic()
    marks = "!" * 249 + "."

    async def pathological(pattern: str, document: str) -> None:
        result = await toolset.call("jev_extract", extract(pattern, document))
        slow[pattern] = (time.monotonic() - started, text_of(result))

    async def heavy_compile(pattern: str) -> None:
        arguments = extract(pattern, marks)
        arguments["fields"][0]["flags"] = "i"
        result = await toolset.call("jev_extract", arguments)
        compiled[pattern] = json.loads(text_of(result))["results"][0]["value"]

    async def benign() -> None:
        while len(slow) < len(CATASTROPHIC):
            began = time.monotonic() if served else started
            extracted = await toolset.call("jev_extract", BENIGN)
            screened = await toolset.call("jev_screen", {"text": "hello"})
            assert '"value": "ABC-123"' in text_of(extracted)
            assert not screened.is_error
            served.append(time.monotonic() - began)
            await anyio.sleep(0.05)

    try:
        async with anyio.create_task_group() as tg:
            for pattern, document in CATASTROPHIC:
                tg.start_soon(pathological, pattern, document)
            for pattern in COMPILE_HEAVY:
                tg.start_soon(heavy_compile, pattern)
            tg.start_soon(benign)
    finally:
        await toolset.aclose()

    # Served throughout the storm, not after it.
    assert len(served) >= 5
    assert max(served) < 0.5
    for pattern, (elapsed, text) in slow.items():
        assert '"status": "invalid_pattern"' in text, pattern
        assert REGEX_TIMEOUT_REASON in text, pattern
        assert REGEX_TIMEOUT_S <= elapsed < REGEX_TIMEOUT_S + SLACK_S, (pattern, elapsed)
    assert compiled == dict.fromkeys(COMPILE_HEAVY, marks)


@pytest.mark.parametrize(("pattern", "document"), REFUSED, ids=[p for p, _ in REFUSED])
async def test_refused_patterns_fail_fast(pattern: str, document: str) -> None:
    toolset = Toolset(Runtime(Settings(), provider_factory=lambda _: FakeProvider(ANSWERS)), TOOLS)
    started = time.monotonic()
    try:
        result = await toolset.call("jev_extract", extract(pattern, document))
    finally:
        await toolset.aclose()
    text = text_of(result)
    assert '"status": "invalid_pattern"' in text
    assert "outside the supported subset" in text
    assert time.monotonic() - started < 0.5


async def test_pool_recovers_after_the_storm() -> None:
    """Every slot that timed out was killed; the next benign call gets a fresh worker and a result."""
    executor = ProcessRegexExecutor(size=2)
    toolset = Toolset(Runtime(Settings(), lambda _: FakeProvider(ANSWERS), executor), TOOLS)
    try:
        async with anyio.create_task_group() as tg:
            for pattern, document in CATASTROPHIC[:2]:
                tg.start_soon(toolset.call, "jev_extract", extract(pattern, document))
        result = await toolset.call("jev_extract", BENIGN)
    finally:
        await toolset.aclose()
    assert '"value": "ABC-123"' in text_of(result)
