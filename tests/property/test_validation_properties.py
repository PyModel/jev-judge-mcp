"""Hypothesis properties for answer validation (ROADMAP P2).

Non-argmax never validates; NaN, infinities, negatives, and sums outside 1 ± tolerance never validate.
"""

import math

from hypothesis import assume, given
from hypothesis import strategies as st

from jev_judge_mcp.validation import (
    ARGMAX_TOLERANCE,
    PROBABILITY_SUM_TOLERANCE,
    margin,
    validate_choice,
    validate_noul,
    validate_score,
)

KEYS = st.lists(st.text(min_size=1, max_size=8), min_size=2, max_size=12, unique=True)
UNIT = st.floats(min_value=0, max_value=1)
BAD_NUMBERS = st.one_of(
    st.just(math.nan),
    st.just(math.inf),
    st.just(-math.inf),
    st.floats(max_value=-1e-300, allow_nan=False, allow_infinity=False),
    st.floats(min_value=1.0000000000000002, allow_nan=False, allow_infinity=False),
)


def js_sum(values: list[float]) -> float:
    total = 0.0
    for value in values:
        total += value
    return total


@st.composite
def distributions(draw: st.DrawFn) -> dict[str, float]:
    """A distribution over distinct non-index keys that sums to 1 within the tolerance."""
    keys = draw(KEYS.filter(lambda ks: not any(k.isascii() and k.isdigit() for k in ks)))
    weights = draw(st.lists(st.floats(min_value=0, max_value=1e6), min_size=len(keys), max_size=len(keys)))
    total = sum(weights)
    assume(total > 0)
    probabilities = {key: weight / total for key, weight in zip(keys, weights, strict=True)}
    assume(abs(js_sum(list(probabilities.values())) - 1) <= PROBABILITY_SUM_TOLERANCE)
    return probabilities


@given(distributions(), st.data())
def test_argmax_choice_validates(probabilities: dict[str, float], data: st.DataObject) -> None:
    top = max(probabilities.values())
    choice = data.draw(st.sampled_from([k for k, p in probabilities.items() if p >= top - ARGMAX_TOLERANCE]))
    answer = validate_choice({"choice": choice, "probabilities": probabilities}, list(probabilities))
    assert answer is not None
    assert answer.choice == choice
    assert margin(answer.probabilities) >= 0


@given(distributions(), st.data())
def test_non_argmax_never_validates(probabilities: dict[str, float], data: st.DataObject) -> None:
    top = max(probabilities.values())
    losers = [k for k, p in probabilities.items() if p < top - ARGMAX_TOLERANCE]
    assume(losers)
    choice = data.draw(st.sampled_from(losers))
    assert validate_choice({"choice": choice, "probabilities": probabilities}, list(probabilities)) is None


@given(distributions(), st.data(), BAD_NUMBERS)
def test_non_finite_or_out_of_range_probability_never_validates(
    probabilities: dict[str, float], data: st.DataObject, bad: float
) -> None:
    key = data.draw(st.sampled_from(list(probabilities)))
    choice = data.draw(st.sampled_from(list(probabilities)))
    corrupted = {**probabilities, key: bad}
    assert validate_choice({"choice": choice, "probabilities": corrupted}, list(probabilities)) is None


@given(st.lists(UNIT, min_size=2, max_size=12), st.data())
def test_sum_outside_tolerance_never_validates(values: list[float], data: st.DataObject) -> None:
    assume(abs(js_sum(values) - 1) > PROBABILITY_SUM_TOLERANCE)
    probabilities = {f"k{i}": value for i, value in enumerate(values)}
    choice = data.draw(st.sampled_from(list(probabilities)))
    assert validate_choice({"choice": choice, "probabilities": probabilities}, list(probabilities)) is None


@given(distributions(), st.text(max_size=8))
def test_key_set_must_match_exactly(probabilities: dict[str, float], extra: str) -> None:
    keys = list(probabilities)
    choice = max(probabilities, key=lambda k: probabilities[k])
    assume(extra not in probabilities)
    answer = {"choice": choice, "probabilities": probabilities}
    assert validate_choice(answer, [*keys, extra]) is None
    assert validate_choice(answer, [k for k in keys if k != choice] or ["x"]) is None


@given(UNIT)
def test_noul_in_unit_interval_validates(value: float) -> None:
    assert validate_noul({"noul": value}) == value


@given(BAD_NUMBERS)
def test_noul_outside_unit_interval_never_validates(value: float) -> None:
    assert validate_noul({"noul": value}) is None


@given(st.floats(min_value=0, max_value=2), st.one_of(UNIT, BAD_NUMBERS))
def test_score_in_range_validates_and_bad_confidence_is_unknown(score: float, confidence: float) -> None:
    answer = validate_score({"score": score, "confidence": confidence})
    assert answer is not None
    assert answer.confidence == (confidence if 0 <= confidence <= 1 else None)


@given(st.one_of(BAD_NUMBERS.filter(lambda v: not 0 <= v <= 2), st.floats(min_value=2.0000000000000004)))
def test_score_outside_range_never_validates(score: float) -> None:
    assert validate_score({"score": score, "confidence": 0.9}) is None
