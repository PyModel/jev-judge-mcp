"""Score answer validation (`validateScoreAnswer`, `index.ts:1159-1168`)."""

import math

from jev_judge_mcp.domain.answers import RawAnswer, RubricAnswer, ScoreAnswer
from jev_judge_mcp.domain.json import as_number, is_json_object
from jev_judge_mcp.serialize import js_key_order
from jev_judge_mcp.validation.choice import PROBABILITY_SUM_TOLERANCE
from jev_judge_mcp.validation.numbers import confidence_of

MAX_SCORE = 2
"""Hardcoded in `validateScoreAnswer` (`index.ts:1161`), independent of the question's rubric length."""


def validate_score(answer: RawAnswer) -> ScoreAnswer | None:
    """The answer if `score` is a finite number in [0, 2], else `None`. A bad confidence becomes `None`."""
    if not is_json_object(answer):
        return None
    score = as_number(answer.get("score"))
    if score is None or not math.isfinite(score) or score < 0 or score > MAX_SCORE:
        return None
    return ScoreAnswer(score=score, confidence=confidence_of(answer))


def validate_rubric_answer(answer: RawAnswer, level_count: int) -> RubricAnswer | None:
    """The answer if it is a well-formed Score on a rubric of `level_count` levels, else `None`.

    Well-formed: `validate_score` accepts it, `score` also lands inside the rubric (`0..n-1`; the
    shared validator's `[0, 2]` window is wider than a short rubric), and `probabilities` has
    exactly the keys `"0".."n-1"`, each a finite number in [0, 1], summing to 1 within
    `PROBABILITY_SUM_TOLERANCE` in JS key order. A malformed component rejects the whole answer:
    the extension tool has no partial-answer shape to project (ADR-0048).
    """
    parsed = validate_score(answer)
    if parsed is None or not is_json_object(answer) or parsed.score > level_count - 1:
        return None
    raw = answer.get("probabilities")
    if not is_json_object(raw):
        return None
    expected = {str(index) for index in range(level_count)}
    keys = list(raw)
    if len(keys) != level_count or any(key not in expected for key in keys):
        return None
    probabilities: dict[str, float] = {}
    for key in keys:
        value = as_number(raw[key])
        if value is None or not math.isfinite(value) or value < 0 or value > 1:
            return None
        probabilities[key] = value
    # JS sums Object.values left to right in JS property order, one float64 add at a time (ADR: JS
    # float semantics); Python's sum() compensates float error since 3.12, so fold by hand.
    total = 0.0
    for key in js_key_order(keys):
        total += probabilities[key]
    if abs(total - 1) > PROBABILITY_SUM_TOLERANCE:
        return None
    return RubricAnswer(score=parsed.score, probabilities=probabilities, confidence=parsed.confidence)
