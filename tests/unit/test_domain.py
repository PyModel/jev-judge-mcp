"""Canonical questions serialize to the reference's wire shape (`@typesafe-ai/sdk` 0.6.0)."""

import json

import pytest

from jev_judge_mcp.domain import (
    ChoiceQuestion,
    NoulCriteria,
    NoulQuestion,
    ScoreQuestion,
    Usage,
    decode_json,
    questions_to_wire,
)
from jev_judge_mcp.serialize import stringify
from tests.support.fixtures import fixture_by_id

FIXTURE = fixture_by_id("unicode-astral/find-truncation-splits-surrogate-pair")


def test_questions_match_a_recorded_request() -> None:
    recorded = FIXTURE.payload["calls"][0]["exchanges"][0]["request"]["body"]["questions"]
    query = "which note ends in an emoji"
    questions = {
        "best": ChoiceQuestion(
            f'Which candidate contains the best answer to: "{query}"?', {"split": None, "plain": None}
        ),
        "exists": NoulQuestion(
            f'Does any candidate address or answer: "{query}"?',
            NoulCriteria(
                true="At least one candidate states or directly implies the answer", false="No candidate addresses this"
            ),
        ),
    }
    wire = questions_to_wire(questions)
    assert wire == recorded
    assert json.dumps(wire) == json.dumps(recorded)  # key order included


def test_score_question_wire_shape() -> None:
    question = ScoreQuestion("How wide is the blast radius?", ["Narrow", "Moderate", "Wide"])
    assert stringify(question.to_wire()) == stringify(
        {"type": "score", "instructions": "How wide is the blast radius?", "criteria": ["Narrow", "Moderate", "Wide"]}
    )


def test_score_question_needs_two_levels() -> None:
    with pytest.raises(ValueError, match="at least two"):
        ScoreQuestion("q", ["only"])


def test_usage_defaults_to_zero() -> None:
    assert Usage().to_wire() == {"input_tokens": 0, "output_tokens": 0}
    assert list(Usage(3, 4).to_wire()) == ["input_tokens", "output_tokens"]


@pytest.mark.parametrize(
    ("text", "parsed"),
    [
        ('{"a": [1, 2.5, null]}', {"a": [1, 2.5, None]}),
        ('"x\\ud83dy"', "x\ud83dy"),  # JSON.parse keeps a lone surrogate escape
        ('{"a": 1e400}', {"a": float("inf")}),  # an overflowing literal is a number, as in JS
    ],
    ids=["object", "lone-surrogate", "overflow"],
)
def test_decode_json_reads_what_json_parse_reads(text: str, parsed: object) -> None:
    assert decode_json(text) == parsed


@pytest.mark.parametrize(
    "text",
    ["NaN", '{"a": Infinity}', "[-Infinity]", "\ufeff{}", "", "[" * 100_000],
    ids=["nan", "infinity", "negative-infinity", "bom", "empty", "deep"],
)
def test_decode_json_rejects_what_json_parse_rejects(text: str) -> None:
    with pytest.raises(ValueError):
        decode_json(text)
