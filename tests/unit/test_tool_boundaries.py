"""Budget edges the parity fixtures do not record (tests/parity/README.md): exactly at a cap passes."""

from typing import assert_type

import pytest

from jev_judge_mcp.policy import (
    DEFAULT_COMPOSITE_FLOOR,
    THRESHOLD_INVARIANT_MESSAGE,
    PolicyThresholds,
    ThresholdInvariant,
    resolve_policy_thresholds,
)
from jev_judge_mcp.tools.base import ToolError
from jev_judge_mcp.tools.review_half import review_settings
from tests.support.jev import call_tool

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


async def test_classify_pair_cap_is_separate_from_the_provider_window() -> None:
    items = [{"text": f"item {i}"} for i in range(64)]
    at_cap = await call_tool("jev_classify", {"items": items, "classes": [{"description": "c"}] * 125}, {})
    assert not at_cap.is_error and len(at_cap.requests) == 1
    assert at_cap.payload["summary"]["invalid_response"] == 64

    over = await call_tool("jev_classify", {"items": items, "classes": [{"description": "c"}] * 126}, {})
    assert over.is_error
    assert over.text == (
        "Batch too large: 64 items x 126 classes exceeds the 8,000 item-class budget. Split the batch."
    )
    assert over.requests == []


async def test_rerank_candidate_cap_is_separate_from_the_provider_window() -> None:
    at_cap = await call_tool("jev_rerank", {"query": "q", "candidates": [{"text": "a" * 2000}] * 50}, {})
    assert not at_cap.is_error and len(at_cap.requests) == 1
    assert at_cap.payload["status"] == "invalid_response"

    over = await call_tool(
        "jev_rerank", {"query": "q", "candidates": [{"text": "a" * 2000}] * 50 + [{"text": "b"}]}, {}
    )
    assert over.is_error
    assert (
        over.text
        == "Batch too large: 100001 candidate characters exceeds the 100000 character budget. Split the batch."
    )
    assert over.requests == []


def test_a_broken_threshold_pair_is_not_a_value_error() -> None:
    """The resolver returns the invariant. It is not an exception, so `except ValueError` cannot catch it."""
    broken = resolve_policy_thresholds(0.6, 0.7)
    assert_type(broken, PolicyThresholds | ThresholdInvariant)
    assert isinstance(broken, ThresholdInvariant)
    assert not isinstance(broken, BaseException)
    assert not issubclass(ThresholdInvariant, BaseException)
    assert broken.message == THRESHOLD_INVARIANT_MESSAGE

    held = resolve_policy_thresholds(0.8, 0.5)
    assert isinstance(held, PolicyThresholds)
    assert_type(held, PolicyThresholds)


def test_review_settings_reports_the_invariant_text_unchanged() -> None:
    """The tool error is the reference sentence, with no chained ValueError."""
    with pytest.raises(ToolError) as caught:
        review_settings({"auto_accept": 0.6, "review_at": 0.7})
    assert str(caught.value) == THRESHOLD_INVARIANT_MESSAGE
    assert caught.value.__cause__ is None

    settings = review_settings({})
    assert settings.thresholds == PolicyThresholds(auto_accept=0.8, review_at=0.5)
    assert settings.composite_floor == DEFAULT_COMPOSITE_FLOOR


@pytest.mark.parametrize(
    ("arguments", "item_ids", "class_ids"),
    [
        (
            {
                "items": [{"text": "a"}, {"id": "item0", "text": "b"}],
                "classes": [{"id": "x", "description": "x"}, {"id": "y", "description": "y"}],
            },
            ["item2", "item0"],
            ["x", "y"],
        ),
        (
            {
                "items": [{"id": "item1", "text": "a"}, {"text": "b"}],
                "classes": [{"id": "x", "description": "x"}, {"id": "y", "description": "y"}],
            },
            ["item1", "item2"],
            ["x", "y"],
        ),
        (
            {
                "items": [{"id": "a", "text": "a"}, {"id": "b", "text": "b"}],
                "classes": [{"description": "x"}, {"id": "class0", "description": "y"}],
            },
            ["a", "b"],
            ["class2", "class0"],
        ),
        (
            {
                "items": [{"id": "a", "text": "a"}, {"id": "b", "text": "b"}],
                "classes": [{"id": "class1", "description": "x"}, {"description": "y"}],
            },
            ["a", "b"],
            ["class1", "class2"],
        ),
    ],
    ids=["omitted-item-then-item0", "item1-then-omitted", "omitted-class-then-class0", "class1-then-omitted"],
)
async def test_classify_generated_ids_preserve_caller_ids(
    arguments: dict[str, object], item_ids: list[str], class_ids: list[str]
) -> None:
    answers = {
        "i0": {"choice": "c0", "probabilities": {"c0": 0.97, "c1": 0.03}},
        "i1": {"choice": "c1", "probabilities": {"c0": 0.03, "c1": 0.97}},
    }
    outcome = await call_tool("jev_classify", arguments, answers)
    assert not outcome.is_error, outcome.text
    rows = outcome.payload["results"]
    assert [row["id"] for row in rows] == item_ids
    assert [row["classification"] for row in rows] == class_ids
    assert len(outcome.requests) == 1


async def test_classify_over_the_item_cap_stays_auto() -> None:
    """ADR-0014: an item cut is telemetry only; the reference returns auto over cut item text."""
    arguments = {
        "items": [{"id": "t", "text": "x" * 2001}],
        "classes": [{"id": "a", "description": "y" * 2001}, {"id": "b", "description": "b"}],
    }
    answers = {"i0": {"choice": "c0", "probabilities": {"c0": 0.97, "c1": 0.03}, "confidence": 0.9}}
    outcome = await call_tool("jev_classify", arguments, answers)
    assert outcome.payload["results"][0]["decision"] == "auto"
