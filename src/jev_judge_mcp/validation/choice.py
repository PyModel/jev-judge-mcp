"""Choice answer validation (`validateChoiceAnswer`, `index.ts:1174-1198`), `margin` (`lib.ts:120-125`), and
`top_probability`."""

from collections.abc import Iterable, Mapping

from jev_judge_mcp.domain.answers import ChoiceAnswer, RawAnswer
from jev_judge_mcp.domain.json import is_json_object
from jev_judge_mcp.validation.numbers import confidence_kind, confidence_of, validate_distribution

PROBABILITY_SUM_TOLERANCE = 0.01 + 1e-12
"""`lib.ts:11`. Kept as the expression: a 0.99 sum is 0.010000000000000009 from 1 and must pass."""

ARGMAX_TOLERANCE = 1e-9
"""The choice may trail the top probability by at most this much and still count as tied."""


def validate_choice(
    answer: RawAnswer, expected_keys: Iterable[str], *, sum_tolerance: float = PROBABILITY_SUM_TOLERANCE
) -> ChoiceAnswer | None:
    """The answer if it is a well-formed Choice over exactly `expected_keys`, else `None`.

    Well-formed: an object whose `choice` is an expected key and whose `probabilities` object has
    exactly the expected keys, each a finite number in [0, 1], summing to 1 within
    `sum_tolerance`, with the choice's probability within `ARGMAX_TOLERANCE` of the
    maximum. A malformed or out-of-range `confidence` becomes `None` and does not reject the answer.
    `confidence_kind` records whether that `None` is absent or malformed.
    """
    if not is_json_object(answer):
        return None
    choice = answer.get("choice")
    expected = set(expected_keys)
    if not isinstance(choice, str) or choice not in expected:
        return None
    probabilities = validate_distribution(answer.get("probabilities"), expected, sum_tolerance)
    if probabilities is None:
        return None
    if probabilities[choice] < max(probabilities.values()) - ARGMAX_TOLERANCE:
        return None
    return ChoiceAnswer(
        choice=choice,
        probabilities=probabilities,
        confidence=confidence_of(answer),
        confidence_kind=confidence_kind(answer),
    )


def margin(probabilities: Mapping[str, float] | None) -> float:
    """Top probability minus the runner-up; 0 when there is no runner-up (`marginOf`)."""
    ranked = sorted((probabilities or {}).values(), reverse=True)
    if len(ranked) < 2:
        return 0
    return ranked[0] - ranked[1]


def top_probability(answer: ChoiceAnswer) -> float:
    """The chosen label's probability, which policy gates: within `ARGMAX_TOLERANCE` of the maximum."""
    return answer.probabilities[answer.choice]
