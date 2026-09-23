"""jev_score, the extension tool beside the frozen ten (ADR-0048, divergence `score-tool-extension`)."""

from typing import cast

import pytest

from jev_judge_mcp.limits import SCORE
from jev_judge_mcp.tools import TOOLS
from jev_judge_mcp.tools.score import DEFINITION
from tests.support.jev import call_tool

pytestmark = pytest.mark.anyio

GOOD = {"score": 0.5, "probabilities": {"0": 0.4, "1": 0.35, "2": 0.25}, "confidence": 0.9}


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


async def test_happy_path_projects_score_distribution_and_nearest() -> None:
    outcome = await call_tool(
        "jev_score",
        {"subject": "Regression risk of the rename", "levels": ["minor", "moderate", "severe"]},
        {"grade": GOOD},
    )
    assert not outcome.is_error, outcome.text
    payload = outcome.payload
    assert payload["tool"] == "jev_score"
    assert payload["provider"] == "compatible"
    assert payload["usage"] == {"input_tokens": 1, "output_tokens": 1}
    assert payload["score"] == 0.5
    assert payload["nearest_level"] == 0  # 0.4 at level 0 beats 0.35 at level 1
    assert payload["probabilities"] == {"0": 0.4, "1": 0.35, "2": 0.25}
    assert payload["confidence"] == 0.9
    assert payload["status"] == "ok"
    assert payload["levels"] == ["minor", "moderate", "severe"]


async def test_the_request_carries_one_score_question_with_the_rubric_as_criteria() -> None:
    outcome = await call_tool(
        "jev_score",
        {"subject": "How severe?", "context": "a prod incident", "levels": ["low", "high"]},
        {"grade": {"score": 1.0, "probabilities": {"0": 0.0, "1": 1.0}}},
    )
    assert not outcome.is_error
    state, questions = outcome.requests[0]
    request_state = cast(dict[str, object], state)
    assert request_state["subject"] == "How severe?"
    assert request_state["context"] == "a prod incident"
    assert list(questions) == ["grade"]
    wire = cast(dict[str, object], questions["grade"])
    assert wire["type"] == "score"
    assert wire["criteria"] == ["low", "high"]
    assert list(wire) == ["type", "instructions", "criteria"]


async def test_a_nearest_level_tie_goes_to_the_lower_level() -> None:
    outcome = await call_tool(
        "jev_score",
        {"subject": "borderline", "levels": ["low", "high"]},
        {"grade": {"score": 0.5, "probabilities": {"0": 0.5, "1": 0.5}}},
    )
    assert outcome.payload["nearest_level"] == 0


async def test_a_malformed_answer_fails_closed_but_keeps_the_frame() -> None:
    outcome = await call_tool(
        "jev_score",
        {"subject": "x", "levels": ["low", "high"]},
        {"grade": {"score": 0.5, "probabilities": {"0": 0.5, "1": 0.4}}},
    )
    assert not outcome.is_error
    payload = outcome.payload
    assert payload["status"] == "invalid_response"
    assert payload["score"] is None and payload["nearest_level"] is None
    assert payload["probabilities"] is None and payload["confidence"] is None
    assert payload["usage"] == {"input_tokens": 1, "output_tokens": 1}


async def test_a_score_inside_the_shared_window_but_outside_the_rubric_is_invalid() -> None:
    """validate_score accepts [0, 2]; a 2-level rubric only reaches 1."""
    outcome = await call_tool(
        "jev_score",
        {"subject": "x", "levels": ["low", "high"]},
        {"grade": {"score": 1.5, "probabilities": {"0": 0.25, "1": 0.75}}},
    )
    assert outcome.payload["status"] == "invalid_response"


async def test_caps_reject_out_of_scale_inputs() -> None:
    too_few = await call_tool("jev_score", {"subject": "x", "levels": ["only"]}, {"grade": GOOD})
    too_many = await call_tool(
        "jev_score", {"subject": "x", "levels": [f"level{i}" for i in range(SCORE.levels_max + 1)]}, {"grade": GOOD}
    )
    too_long = await call_tool(
        "jev_score", {"subject": "x" * (SCORE.subject_max + 1), "levels": ["a", "b"]}, {"grade": GOOD}
    )
    for outcome in (too_few, too_many, too_long):
        assert outcome.is_error
        assert "Invalid params" in outcome.text or "invalid" in outcome.text.lower()


def test_the_schema_carries_the_adr_owned_caps() -> None:
    schema = DEFINITION.input_schema
    levels = schema["properties"]["levels"]
    assert levels["minItems"] == SCORE.levels_min
    assert levels["maxItems"] == SCORE.levels_max
    assert levels["items"]["minLength"] == SCORE.level_units_min
    assert levels["items"]["maxLength"] == SCORE.level_units_max
    subject = schema["properties"]["subject"]
    assert subject["minLength"] == SCORE.subject_min
    assert subject["maxLength"] == SCORE.subject_max
    assert schema["properties"]["context"]["maxLength"] == SCORE.context_max
    assert schema["required"] == ["subject", "levels"]
    assert schema["additionalProperties"] is False


def test_score_is_published_after_the_snapshot_ten() -> None:
    names = tuple(tool.name for tool in TOOLS)
    assert names[-1] == "jev_score"
    assert len(names) == 11
