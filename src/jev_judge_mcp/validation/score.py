"""Score answer validation (`validateScoreAnswer`, `index.ts:1159-1168`, and ADR-0048)."""

import math

from jev_judge_mcp.domain.answers import RUBRIC_SCORE_MAX, RawAnswer, RubricAnswer, ScoreAnswer
from jev_judge_mcp.domain.json import as_number, is_json_object
from jev_judge_mcp.validation.choice import PROBABILITY_SUM_TOLERANCE
from jev_judge_mcp.validation.numbers import confidence_of, validate_distribution

MAX_SCORE = RUBRIC_SCORE_MAX
"""The domain's upper bound for a fixed three-level review rubric."""

SCORE_ROUNDING_HALF_UNIT = 0.005
"""The provider rounds the score and each probability to hundredths. Their maximum
combined rounding error is half a hundredth times one plus the sum of the level indices.
"""


def validate_score(answer: RawAnswer) -> ScoreAnswer | None:
    """Validate a fixed three-level score. A supplied distribution must agree with the score."""
    if not is_json_object(answer):
        return None
    if "probabilities" in answer:
        rubric = validate_rubric_answer(answer, MAX_SCORE + 1)
        return None if rubric is None else ScoreAnswer(score=rubric.score, confidence=rubric.confidence)
    score = as_number(answer.get("score"))
    if score is None or not 0 <= score <= MAX_SCORE:
        return None
    return ScoreAnswer(score=score, confidence=confidence_of(answer))


def validate_rubric_answer(answer: RawAnswer, level_count: int) -> RubricAnswer | None:
    """Validate the score, its level distribution, and agreement with that distribution's weighted mean."""
    if not is_json_object(answer):
        return None
    score = as_number(answer.get("score"))
    if score is None or not 0 <= score <= level_count - 1:
        return None
    levels = range(level_count)
    probabilities = validate_distribution(
        answer.get("probabilities"), {str(index) for index in levels}, PROBABILITY_SUM_TOLERANCE
    )
    if probabilities is None:
        return None
    expected_score = math.fsum(index * probabilities[str(index)] for index in levels)
    tolerance = SCORE_ROUNDING_HALF_UNIT * (1 + sum(levels))
    if abs(score - expected_score) > tolerance + 1e-12:
        return None
    return RubricAnswer(score=score, probabilities=probabilities, confidence=confidence_of(answer))
