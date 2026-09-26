"""`validate_noul` and `validate_score`: the screen/find/rerank Noul matrix and the review Score cases."""

import math

import pytest

from jev_judge_mcp.domain import ScoreAnswer, is_json_object
from jev_judge_mcp.validation import validate_noul, validate_rubric_answer, validate_score

# jev_screen and jev_find reject each of these as a Noul value, and a missing answer entirely.
BAD_NOULS: list[object] = [
    None,
    "0.1",
    "0",
    -0.1,
    1.1,
    -1,
    2,
    True,
    False,
    math.nan,
    math.inf,
    -math.inf,
    2**1100,
    [0.5],
    {},
]


@pytest.mark.parametrize("noul", BAD_NOULS)
def test_bad_noul_rejected(noul: object) -> None:
    assert validate_noul({"noul": noul}) is None


@pytest.mark.parametrize("answer", [None, True, 0, "bad", [], {}, {"noul": None}, {"yes": 0.5}, [0.5]])
def test_missing_or_non_record_noul_answer_rejected(answer: object) -> None:
    assert validate_noul(answer) is None


@pytest.mark.parametrize(("noul", "expected"), [(0, 0.0), (1, 1.0), (0.0, 0.0), (-0.0, 0.0), (0.5, 0.5), (1.0, 1.0)])
def test_boundary_noul_accepted(noul: object, expected: float) -> None:
    # exists=0 is a valid "absent", injection=0 a valid "pass": callers test `is None`, never truthiness.
    value = validate_noul({"noul": noul})
    assert value is not None
    assert value == expected


def test_noul_above_one_rejected_beside_valid_sibling() -> None:
    assert validate_noul({"noul": 1.5}) is None
    assert validate_noul({"noul": 0.9}) == 0.9


@pytest.mark.parametrize("score", [2.5, -0.1, 3, math.nan, math.inf, "1", None, True, [1]])
def test_bad_score_rejected(score: object) -> None:
    assert validate_score({"score": score, "confidence": 0.9}) is None


@pytest.mark.parametrize("answer", [None, 0, "bad", [], {}, {"confidence": 0.9}])
def test_missing_score_answer_rejected(answer: object) -> None:
    assert validate_score(answer) is None


@pytest.mark.parametrize("score", [0, 1, 2, 0.0, 1.5, 2.0])
def test_score_bounds_inclusive(score: float) -> None:
    assert validate_score({"score": score, "confidence": 0.9}) == ScoreAnswer(score=float(score), confidence=0.9)


@pytest.mark.parametrize("confidence", [None, "0.9", 1.1, -0.1, math.nan, True, {}])
def test_bad_score_confidence_is_unknown_not_zero(confidence: object) -> None:
    raw: dict[str, object] = {"score": 2}
    if confidence is not None:
        raw["confidence"] = confidence
    assert validate_score(raw) == ScoreAnswer(score=2.0, confidence=None)


def test_zero_score_confidence_is_known() -> None:
    assert validate_score({"score": 0, "confidence": 0}) == ScoreAnswer(score=0.0, confidence=0.0)


RUBRIC_FIVE: dict[str, object] = {
    "score": 3.25,
    "probabilities": {"0": 0.1, "1": 0.1, "2": 0.1, "3": 0.6, "4": 0.1},
    "confidence": 0.9,
}


@pytest.mark.parametrize("score", [0, 2, 3, 3.5, 4])
def test_rubric_answer_accepts_the_whole_rubric_window(score: float) -> None:
    """The rubric's own 0..n-1 bound governs (ADR-0048), not validate_score's [0, 2]."""
    answer = validate_rubric_answer({**RUBRIC_FIVE, "score": score}, 5)
    assert answer is not None
    assert answer.score == float(score)
    assert answer.confidence == 0.9


@pytest.mark.parametrize("score", [-0.1, 4.5, 5])
def test_rubric_answer_rejects_a_score_outside_the_rubric(score: float) -> None:
    assert validate_rubric_answer({**RUBRIC_FIVE, "score": score}, 5) is None


def test_rubric_answer_still_uses_each_rubrics_own_window() -> None:
    """A short rubric keeps rejecting a score inside the wider window: 2 is off a 2-level rubric."""
    two_level = {"score": 2, "probabilities": {"0": 0.0, "1": 1.0}}
    assert validate_rubric_answer(two_level, 2) is None
    assert validate_rubric_answer({"score": 1, "probabilities": {"0": 0.0, "1": 1.0}}, 2) is not None


@pytest.mark.parametrize(
    ("value", "expected"),
    [({}, True), ({"a": 1}, True), (None, False), ([], False), ("bad", False), (0, False), (True, False)],
)
def test_is_json_object(value: object, expected: bool) -> None:
    assert is_json_object(value) is expected
