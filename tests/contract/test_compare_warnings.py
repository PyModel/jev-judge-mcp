"""The compare aspect-contradiction warning (ADR-0052, divergence `compare-aspect-contradiction-warning`).

The reference judges the overall independently of the aspects and never connects them. Python
keeps the overall unchanged and adds a decide-style `warnings` field only when an aspect reports
`contradicts` under a non-`contradicts` overall — so every unaffected output stays byte-identical
to the reference. The parity corpus still has no such call, so its replay needs no expectation.
A live answer that did fire the warning is replayed from `tests/fixtures/compare/`, outside that
corpus, so the warning is pinned to a real judgment and not only to constructed answers.
"""

import json
from pathlib import Path
from typing import Any

import pytest

from tests.support.fixtures import Fixture, FixtureCall
from tests.support.jev import call_tool
from tests.support.replay import replay_call

LIVE_FIXTURE = Path(__file__).parents[1] / "fixtures" / "compare" / "live-humidity-aspect-contradiction.json"

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


ARGUMENTS = {
    "passage_a": "The launch is on May 1.",
    "passage_b": "The launch slipped to June 1.",
    "aspects": ["price", "launch date"],
}
CONFIDENT = 0.99
MARGIN = 0.97


def answer(relation: str, others: dict[str, float] | None = None) -> dict[str, Any]:
    probabilities = {relation: CONFIDENT, **(others or {})}
    return {"choice": relation, "probabilities": probabilities, "confidence": CONFIDENT}


THREE_WAY = {"same_fact": 0.005, "contradicts": 0.005, "different_facts": 0.005}


async def test_an_aspect_contradiction_under_a_non_contradicts_overall_warns() -> None:
    answers = {
        "overall": answer("same_fact", {"contradicts": 0.005, "different_facts": 0.005}),
        "aspect_0": answer("same_fact", {"contradicts": 0.005, "different_facts": 0.005}),
        "aspect_1": answer("contradicts", {"same_fact": 0.005, "different_facts": 0.005}),
    }
    outcome = await call_tool("jev_compare", ARGUMENTS, answers)
    assert not outcome.is_error, outcome.text
    payload = outcome.payload
    assert payload["overall"]["relation"] == "same_fact"
    assert [aspect["relation"] for aspect in payload["aspects"]] == ["same_fact", "contradicts"]
    assert payload["warnings"] == [
        'Aspect "launch date" reports contradicts while the overall relation does not; inspect before acting'
    ]


async def test_several_contradicting_aspects_are_named_in_order() -> None:
    answers = {
        "overall": answer("different_facts", {"contradicts": 0.005, "same_fact": 0.005}),
        "aspect_0": answer("contradicts", {"same_fact": 0.005, "different_facts": 0.005}),
        "aspect_1": answer("same_fact", {"contradicts": 0.005, "different_facts": 0.005}),
        "aspect_2": answer("contradicts", {"same_fact": 0.005, "different_facts": 0.005}),
    }
    arguments = {**ARGUMENTS, "aspects": ["price", "launch date", "method"]}
    outcome = await call_tool("jev_compare", arguments, answers)
    assert not outcome.is_error, outcome.text
    assert outcome.payload["warnings"] == [
        'Aspects "price", "method" report contradicts while the overall relation does not; inspect before acting'
    ]


async def test_one_contradicting_aspect_uses_the_singular() -> None:
    answers = {
        "overall": answer("same_fact", {"contradicts": 0.005, "different_facts": 0.005}),
        "aspect_0": answer("contradicts", {"same_fact": 0.005, "different_facts": 0.005}),
    }
    arguments = {**ARGUMENTS, "aspects": ["price"]}
    outcome = await call_tool("jev_compare", arguments, answers)
    assert not outcome.is_error, outcome.text
    assert outcome.payload["warnings"] == [
        'Aspect "price" reports contradicts while the overall relation does not; inspect before acting'
    ]


async def test_a_contradicts_overall_never_warns() -> None:
    answers = {
        "overall": answer("contradicts", {"same_fact": 0.005, "different_facts": 0.005}),
        "aspect_0": answer("contradicts", {"same_fact": 0.005, "different_facts": 0.005}),
    }
    outcome = await call_tool("jev_compare", ARGUMENTS, answers)
    assert not outcome.is_error, outcome.text
    assert "warnings" not in outcome.payload


async def test_without_an_aspect_contradiction_the_output_stays_byte_identical() -> None:
    """No warning key: `set(payload)` is exactly the reference's compare keys."""
    answers = {
        "overall": answer("same_fact", {"contradicts": 0.005, "different_facts": 0.005}),
        "aspect_0": answer("same_fact", {"contradicts": 0.005, "different_facts": 0.005}),
        "aspect_1": answer("different_facts", {"contradicts": 0.005, "same_fact": 0.005}),
    }
    outcome = await call_tool("jev_compare", ARGUMENTS, answers)
    assert not outcome.is_error, outcome.text
    payload = outcome.payload
    assert "warnings" not in payload
    assert list(payload) == ["tool", "model", "provider", "overall", "aspects", "thresholds", "usage"]
    # The low-margin path never warns either: the action, not an aspect relation, carries the doubt.
    low_margin = {
        "choice": "same_fact",
        "probabilities": {"same_fact": 0.5, "contradicts": 0.3, "different_facts": 0.2},
        "confidence": 0.5,
    }
    low = {"overall": dict(low_margin), "aspect_0": dict(low_margin)}
    outcome = await call_tool("jev_compare", ARGUMENTS, low)
    assert not outcome.is_error, outcome.text
    assert "warnings" not in outcome.payload
    assert outcome.payload["overall"]["decision"] == "review"


async def test_a_fail_closed_overall_still_warns_on_a_contradicting_aspect() -> None:
    outcome = await call_tool(
        "jev_compare",
        ARGUMENTS,
        {
            "overall": {"choice": "nope", "probabilities": THREE_WAY, "confidence": CONFIDENT},
            "aspect_0": answer("same_fact", {"contradicts": 0.005, "different_facts": 0.005}),
            "aspect_1": answer("contradicts", {"same_fact": 0.005, "different_facts": 0.005}),
        },
    )
    assert not outcome.is_error, outcome.text
    payload = outcome.payload
    assert payload["overall"]["relation"] is None
    assert payload["overall"]["status"] == "invalid_response"
    assert payload["warnings"] == [
        'Aspect "launch date" reports contradicts while the overall relation does not; inspect before acting'
    ]


async def test_the_live_humidity_contradiction_replays_the_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    """The 2026-09-24 live answer (ADR-0052): overall same_fact, humidity contradicts."""
    payload = json.loads(LIVE_FIXTURE.read_text(encoding="utf-8"))
    call = FixtureCall(Fixture(LIVE_FIXTURE, payload), 0, payload["calls"][0])
    await replay_call(call, monkeypatch)
    body = json.loads(call.payload["result"]["content"][0]["text"])
    assert body["overall"]["relation"] == "same_fact"
    assert [item["relation"] for item in body["aspects"]] == [
        "same_fact",
        "same_fact",
        "same_fact",
        "same_fact",
        "contradicts",
    ]
    assert body["aspects"][-1]["aspect"] == "humidity"
    assert body["warnings"] == [
        'Aspect "humidity" reports contradicts while the overall relation does not; inspect before acting'
    ]
