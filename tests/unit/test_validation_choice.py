"""`validate_choice`, `margin` and `top_probability`: every Choice edge case in the reference unit and mock suites.

Tables are ported from `test/mock.test.mjs` at 69ffb4b (BAD_BEST, BAD_RELATIONS, BAD_SOURCES, the
classify argmax and tie cases, the gate bad-claim lists, the 0.01 sum-delta case). The verify-only
Q4 confidence rule and extract's inline Q2 check belong to those tools (P5), not to this validator.
"""

import math
from typing import cast

import pytest

from jev_judge_mcp.domain import ChoiceAnswer
from jev_judge_mcp.validation import PROBABILITY_SUM_TOLERANCE, margin, top_probability, validate_choice

FIND_KEYS = ["a", "b"]
RELATION_KEYS = ["supports", "contradicts", "says_nothing"]
CLASSIFY_KEYS = ["c0", "c1", "c2"]
SOURCE_KEYS = ["a", "b", "none"]
CLAIM_KEYS = ["verified", "contradicted", "unsupported"]


def pick(choice: str, keys: list[str]) -> dict[str, object]:
    """The mock suite's `pick`: the chosen key at 0.95, the rest share 0.05."""
    rest = 0.05 / (len(keys) - 1)
    return {"choice": choice, "confidence": 0.99, "probabilities": {k: 0.95 if k == choice else rest for k in keys}}


BAD_BEST: list[object] = [
    None,
    True,
    "bad",
    [],
    {},
    {"choice": "a"},
    *[
        {"choice": "a", "probabilities": probabilities}
        for probabilities in cast(
            "list[object]",
            [
                {},
                {"alien": 1},
                {"a": 1},
                {"a": 1, "b": 0, "alien": 0},
                {"a": None, "b": 1},
                {"a": "0.9", "b": 0.1},
                {"a": 1.1, "b": -0.1},
                {"a": 0.8, "b": 0.8},
                {"a": 0, "b": 1},
                [],
                "bad",
                1,
            ],
        )
    ],
    {"choice": 1, "probabilities": {"a": 1, "b": 0}},
    {"choice": "alien", "probabilities": {"a": 1, "b": 0}},
]


@pytest.mark.parametrize("answer", BAD_BEST)
def test_find_bad_best_rejected(answer: object) -> None:
    assert validate_choice(answer, FIND_KEYS) is None


BAD_RELATIONS: list[object] = [
    None,
    True,
    "bad",
    [],
    {},
    *[
        {**pick("supports", RELATION_KEYS), "choice": choice}
        for choice in cast("list[object]", ["toString", "__proto__", "constructor", 0, True, {}, ["supports"]])
    ],
    *[
        {"choice": "supports", "confidence": 0.99, "probabilities": probabilities}
        for probabilities in cast(
            "list[object]",
            [
                None,
                {},
                [],
                "bad",
                1,
                {"supports": 1},
                {"supports": 1, "contradicts": 0, "says_nothing": 0, "alien": 0},
                {"supports": "0.9", "contradicts": 0.1, "says_nothing": 0},
                {"supports": None, "contradicts": 1, "says_nothing": 0},
                {"supports": 1.2, "contradicts": -0.2, "says_nothing": 0},
                {"supports": 0.9, "contradicts": 0.9, "says_nothing": 0},
                {"supports": 0.1, "contradicts": 0.9, "says_nothing": 0},
            ],
        )
    ],
    {"choice": "supports", "confidence": 0.99},
]


@pytest.mark.parametrize("answer", BAD_RELATIONS)
def test_verify_bad_relation_rejected(answer: object) -> None:
    assert validate_choice(answer, RELATION_KEYS) is None


@pytest.mark.parametrize("confidence", ["0.99", 2, -1, True, False, {}, [], math.nan, math.inf, 1.7])
def test_malformed_confidence_becomes_none_without_rejecting(confidence: object) -> None:
    # jev_verify rejects these (quirk Q4, P5); the shared validator keeps the pick with unknown confidence,
    # as jev_decide does ("normalizes non-finite confidence to null without discarding the pick").
    answer = validate_choice({**pick("supports", RELATION_KEYS), "confidence": confidence}, RELATION_KEYS)
    assert answer is not None
    assert answer.choice == "supports"
    assert answer.confidence is None
    assert answer.confidence_kind == "malformed"


@pytest.mark.parametrize(("confidence", "expected"), [(None, None), (0, 0.0), (1, 1.0), (0.5, 0.5), (-0.0, 0.0)])
def test_valid_confidence_is_kept(confidence: object, expected: float | None) -> None:
    raw = pick("supports", RELATION_KEYS)
    if confidence is None:
        del raw["confidence"]
    else:
        raw["confidence"] = confidence
    answer = validate_choice(raw, RELATION_KEYS)
    assert answer is not None
    assert answer.confidence == expected
    assert answer.confidence_kind == ("absent" if expected is None else "number")


def test_json_null_confidence_is_absent() -> None:
    raw = pick("supports", RELATION_KEYS)
    raw["confidence"] = None
    answer = validate_choice(raw, RELATION_KEYS)
    assert answer is not None
    assert answer.confidence is None
    assert answer.confidence_kind == "absent"


def test_bare_float_confidence_is_a_number() -> None:
    """A constructor that only has the wire number cannot say malformed; a float is a number."""
    answer = ChoiceAnswer("b", {"a": 0.1, "b": 0.9}, 0.4)
    assert answer.confidence == 0.4
    assert answer.confidence_kind == "number"
    malformed = ChoiceAnswer("b", {"a": 0.1, "b": 0.9}, None, "malformed")
    assert malformed.confidence is None
    assert malformed.confidence_kind == "malformed"


def test_confidence_kind_must_match_the_wire_number() -> None:
    with pytest.raises(ValueError, match="malformed confidence"):
        ChoiceAnswer("b", {"a": 0.1, "b": 0.9}, 0.4, "malformed")
    with pytest.raises(ValueError, match="number confidence"):
        ChoiceAnswer("b", {"a": 0.1, "b": 0.9}, None, "number")


BAD_SOURCES: list[object] = [
    None,
    "a",
    {},
    [],
    {"choice": "a"},
    {"choice": "alien", "probabilities": {"a": 1, "b": 0, "none": 0}},
    {"choice": "a", "probabilities": {"a": 0.5, "b": 0.5}},
    {"choice": "a", "probabilities": {"a": 0.9, "b": 0.9, "none": 0}},
    {**pick("a", SOURCE_KEYS), "choice": 1},
]


@pytest.mark.parametrize("answer", BAD_SOURCES)
def test_verify_bad_source_rejected(answer: object) -> None:
    assert validate_choice(answer, SOURCE_KEYS) is None


@pytest.mark.parametrize("choice", ["a", "none"])
def test_verify_source_accepts_supplied_ids_and_none(choice: str) -> None:
    answer = validate_choice(pick(choice, SOURCE_KEYS), SOURCE_KEYS)
    assert answer is not None
    assert answer.choice == choice


BAD_CLAIMS: list[dict[str, object]] = [
    {
        "choice": "verified",
        "confidence": 0.99,
        "probabilities": {"verified": 0.6, "contradicted": 0.6, "unsupported": 0.6},
    },
    {
        "choice": "verified",
        "confidence": 0.99,
        "probabilities": {"verified": 1.2, "contradicted": -0.1, "unsupported": -0.1},
    },
    {
        "choice": "verified",
        "confidence": 0.99,
        "probabilities": {"verified": 0.2, "contradicted": 0.7, "unsupported": 0.1},
    },
    {"choice": "verified", "confidence": 0.99, "probabilities": {"verified": 1, "contradicted": 0}},
]


@pytest.mark.parametrize("answer", BAD_CLAIMS)
def test_gate_bad_claims_rejected(answer: object) -> None:
    assert validate_choice(answer, CLAIM_KEYS) is None


def test_off_catalog_choice_rejected() -> None:
    answer = {"choice": "definitely", "confidence": 0.99, "probabilities": {"definitely": 1}}
    assert validate_choice(answer, ["claim0"]) is None
    assert validate_choice(answer, RELATION_KEYS) is None


def test_non_argmax_rejected() -> None:
    assert (
        validate_choice({"choice": "c1", "probabilities": {"c0": 0.9, "c1": 0.05, "c2": 0.05}}, CLASSIFY_KEYS) is None
    )
    decide_keys = ["option_0", "option_1", "ask_user", "investigate", "none"]
    recommendation = {
        "choice": "option_1",
        "confidence": 0.99,
        "probabilities": {"option_0": 0.9, "option_1": 0.04, "ask_user": 0.02, "investigate": 0.02, "none": 0.02},
    }
    assert validate_choice(recommendation, decide_keys) is None
    check = {
        "choice": "contradicted",
        "confidence": 0.9,
        "probabilities": {"supported": 0.8, "contradicted": 0.1, "unknown": 0.1},
    }
    assert validate_choice(check, ["supported", "contradicted", "unknown"]) is None


@pytest.mark.parametrize("choice", ["c0", "c1"])
def test_either_tied_maximum_accepted(choice: str) -> None:
    answer = validate_choice({"choice": choice, "probabilities": {"c0": 0.5, "c1": 0.5, "c2": 0}}, CLASSIFY_KEYS)
    assert answer == ChoiceAnswer(choice=choice, probabilities={"c0": 0.5, "c1": 0.5, "c2": 0.0}, confidence=None)


def test_argmax_tolerance_is_1e_9() -> None:
    inside = {"choice": "c1", "probabilities": {"c0": 0.5, "c1": 0.5 - 5e-10, "c2": 5e-10}}
    outside = {"choice": "c1", "probabilities": {"c0": 0.5, "c1": 0.5 - 2e-9, "c2": 2e-9}}
    assert validate_choice(inside, CLASSIFY_KEYS) is not None
    assert validate_choice(outside, CLASSIFY_KEYS) is None


def test_exact_001_sum_deltas_survive_float_comparison() -> None:
    # |0.33 + 0.33 + 0.33 - 1| is 0.010000000000000009 in IEEE-754, above a bare 0.01.
    thirds = {"choice": "supports", "confidence": None, "probabilities": dict.fromkeys(RELATION_KEYS, 0.33)}
    assert abs(0.33 + 0.33 + 0.33 - 1) > 0.01
    assert validate_choice(thirds, RELATION_KEYS) is not None
    assert (
        validate_choice({"choice": "c0", "probabilities": dict.fromkeys(CLASSIFY_KEYS, 0.33)}, CLASSIFY_KEYS)
        is not None
    )
    # ADR-0012: the shared tolerance accepts 0.3 + 0.3 + 0.39 too; only jev_extract's inline check rejects it.
    uneven = {"choice": "c2", "probabilities": {"c0": 0.3, "c1": 0.3, "c2": 0.39}}
    assert validate_choice(uneven, CLASSIFY_KEYS) is not None
    assert PROBABILITY_SUM_TOLERANCE == 0.01 + 1e-12


@pytest.mark.parametrize("total", [0.98, 1.02, 0.5, 1.5, 0.0])
def test_sum_outside_tolerance_rejected(total: float) -> None:
    probabilities = {"c0": total, "c1": 0.0, "c2": 0.0}
    assert validate_choice({"choice": "c0", "probabilities": probabilities}, CLASSIFY_KEYS) is None


@pytest.mark.parametrize(
    "bad", [math.nan, math.inf, -math.inf, -0.1, 1.0000001, 2**1100, "0.5", None, True, False, [0.5], {"v": 0.5}]
)
def test_non_finite_negative_or_non_number_probability_rejected(bad: object) -> None:
    answer = {"choice": "c0", "probabilities": {"c0": bad, "c1": 0.5, "c2": 0.5}}
    assert validate_choice(answer, CLASSIFY_KEYS) is None


def test_integer_probabilities_are_numbers() -> None:
    answer = validate_choice({"choice": "b", "probabilities": {"a": 0, "b": 1}}, FIND_KEYS)
    assert answer is not None
    assert answer.probabilities == {"a": 0.0, "b": 1.0}


def test_negative_zero_probability_accepted() -> None:
    assert validate_choice({"choice": "b", "probabilities": {"a": -0.0, "b": 1}}, FIND_KEYS) is not None


def test_prototype_names_are_ordinary_keys() -> None:
    # jev_decide accepts a candidate id named constructor; off-catalog prototype names are rejected above.
    keys = ["constructor", "toString"]
    answer = validate_choice(pick("constructor", keys), keys)
    assert answer is not None
    assert answer.choice == "constructor"


def test_expected_keys_are_a_set() -> None:
    assert validate_choice(pick("a", FIND_KEYS), ["a", "b", "a"]) is not None
    assert validate_choice(pick("a", FIND_KEYS), iter(FIND_KEYS)) is not None


def test_probabilities_keep_provider_key_order() -> None:
    answer = validate_choice({"choice": "b", "probabilities": {"b": 0.6, "a": 0.4}}, FIND_KEYS)
    assert answer is not None
    assert list(answer.probabilities) == ["b", "a"]


def test_sum_follows_js_property_order() -> None:
    # JS sums Object.values in property order, array-index keys ascending first: "0", "1", "x".
    # At the tolerance edge the order decides: insertion order passes, JS order does not.
    x, one, zero = 0.33861008860853326, 0.399219470288713, 0.2521704411017537
    assert abs(x + one + zero - 1) <= PROBABILITY_SUM_TOLERANCE
    assert abs(zero + one + x - 1) > PROBABILITY_SUM_TOLERANCE
    answer = {"choice": "1", "probabilities": {"x": x, "1": one, "0": zero}}
    assert validate_choice(answer, ["x", "1", "0"]) is None


def test_margin() -> None:
    assert abs(margin({"a": 0.7, "b": 0.2, "c": 0.1}) - 0.5) < 1e-9
    assert margin({"a": 0.5, "b": 0.5}) == 0
    assert margin({"only": 0.8}) == 0
    assert margin({}) == 0
    assert margin(None) == 0
    assert margin({"billing": 0.95, "sales": 0.025, "technical": 0.025}) == 0.95 - 0.025


def test_top_probability_is_the_chosen_labels_not_the_maximum() -> None:
    """Policy gates the chosen label's probability; within ARGMAX_TOLERANCE it may trail the maximum."""
    assert top_probability(ChoiceAnswer("b", {"a": 0.1, "b": 0.9}, None)) == 0.9
    tied = ChoiceAnswer("a", {"a": 0.5 - 5e-10, "b": 0.5 + 5e-10}, None)
    assert top_probability(tied) == 0.5 - 5e-10
