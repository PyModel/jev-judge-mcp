"""jev_extract's own inline Choice validation (`index.ts:1047-1061`), kept apart from the shared one (Q2, ADR-0012).

It differs from `validate_choice` in two observable ways: the sum tolerance is a bare `<= 0.01`, so a
0.99 sum (0.010000000000000009 from 1) fails here and passes there; and `probabilities` that are not
an object count as `{}` rather than rejecting outright, which rejects all the same because the
expected key set is never empty.
"""

import math
from collections.abc import Iterable

from jev_judge_mcp.domain.answers import ChoiceAnswer, RawAnswer
from jev_judge_mcp.domain.json import as_number, is_json_object
from jev_judge_mcp.serialize import js_key_order
from jev_judge_mcp.validation.choice import ARGMAX_TOLERANCE
from jev_judge_mcp.validation.numbers import confidence_of

EXTRACT_SUM_TOLERANCE = 0.01


def validate_extract_choice(answer: RawAnswer, expected_keys: Iterable[str]) -> ChoiceAnswer | None:
    """The answer if its choice is expected and its probabilities are exactly the expected keys, each a
    finite number in [0, 1], summing to 1 within `<= 0.01`, with the choice at the maximum; else `None`."""
    if not is_json_object(answer):
        return None
    choice = answer.get("choice")
    raw = answer.get("probabilities")
    raw_probabilities: dict[str, object] = raw if is_json_object(raw) else {}
    expected = set(expected_keys)
    keys = list(raw_probabilities)
    if not isinstance(choice, str) or choice not in expected or len(keys) != len(expected):
        return None
    if not all(key in expected for key in keys):
        return None
    probabilities: dict[str, float] = {}
    for key in keys:
        value = as_number(raw_probabilities[key])
        if value is None or not math.isfinite(value) or value < 0 or value > 1:
            return None
        probabilities[key] = value
    total = 0.0
    for key in js_key_order(keys):
        total += probabilities[key]
    if not abs(total - 1) <= EXTRACT_SUM_TOLERANCE:
        return None
    if not probabilities[choice] >= max(probabilities.values()) - ARGMAX_TOLERANCE:
        return None
    return ChoiceAnswer(choice=choice, probabilities=probabilities, confidence=confidence_of(answer))
