"""The ROADMAP P5 fail-closed matrix: no malformed answer yields a permissive action.

Each case starts from answers that make the tool permissive (auto, or pass for jev_screen) and
checks that fact, so no row is vacuous. Then each malformation of one target answer must leave the
tool non-permissive: missing, wrong type, extra key, missing key, non-argmax, off sums, NaN,
infinity, negative, and, where the policy reads it, a malformed confidence.

Sums: the shared validator accepts a sum within `0.01 + 1e-12` of 1, so 0.99 and 1.01 are
well-formed there (ADR-0012 D1) and the off sums are 0.98 and 1.02. jev_extract keeps its own
`<= 0.01` check, where 0.99 and 1.01 already fail.
"""

import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, cast

import pytest

from tests.support.jev import call_tool

pytestmark = pytest.mark.anyio

MISSING = object()

type Mutation = Callable[[Any], Any]


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _with_probabilities(answer: dict[str, Any], change: Callable[[dict[str, Any]], None]) -> dict[str, Any]:
    probabilities = dict(answer["probabilities"])
    change(probabilities)
    return {**answer, "probabilities": probabilities}


def _runner_up(answer: dict[str, Any]) -> str:
    return next(key for key in answer["probabilities"] if key != answer["choice"])


def _shift(answer: dict[str, Any], delta: float) -> dict[str, Any]:
    def change(p: dict[str, Any]) -> None:
        p[answer["choice"]] += delta

    return _with_probabilities(answer, change)


def _set_chosen(value: Any) -> Mutation:
    def mutate(answer: dict[str, Any]) -> dict[str, Any]:
        def change(p: dict[str, Any]) -> None:
            p[answer["choice"]] = value

        return _with_probabilities(answer, change)

    return mutate


def _negative(answer: dict[str, Any]) -> dict[str, Any]:
    other = _runner_up(answer)

    def change(p: dict[str, Any]) -> None:
        p[other] = -0.05
        p[answer["choice"]] += 0.05 + answer["probabilities"][other]

    return _with_probabilities(answer, change)


def _missing_key(answer: dict[str, Any]) -> dict[str, Any]:
    other = _runner_up(answer)

    def change(p: dict[str, Any]) -> None:
        p[answer["choice"]] += p.pop(other)

    return _with_probabilities(answer, change)


def _extra_key(answer: dict[str, Any]) -> dict[str, Any]:
    def change(p: dict[str, Any]) -> None:
        p["not_an_option"] = 0

    return _with_probabilities(answer, change)


def choice_mutations(sum_delta: float) -> dict[str, Mutation]:
    return {
        "missing": lambda _: MISSING,
        "wrong_type": lambda answer: answer["choice"],
        "null": lambda _: None,
        "array": lambda answer: [answer],
        "probabilities_not_object": lambda answer: {**answer, "probabilities": list(answer["probabilities"].values())},
        "choice_not_string": lambda answer: {**answer, "choice": 0},
        "unknown_choice": lambda answer: {**answer, "choice": "not_an_option"},
        "extra_key": _extra_key,
        "missing_key": _missing_key,
        "non_argmax": lambda answer: {**answer, "choice": _runner_up(answer)},
        "sum_low": lambda answer: _shift(answer, -sum_delta),
        "sum_high": lambda answer: _shift(answer, sum_delta),
        "nan": _set_chosen(math.nan),
        "inf": _set_chosen(math.inf),
        "negative": _negative,
        "above_one": _set_chosen(1.5),
        "string_probability": _set_chosen("0.97"),
        "bool_probability": _set_chosen(True),
    }


def _confidence(value: object) -> Mutation:
    return lambda answer: {**answer, "confidence": value}


CONFIDENCE_MUTATIONS: dict[str, Mutation] = {
    f"confidence_{name}": _confidence(value)
    for name, value in {
        "string": "high",
        "nan": math.nan,
        "inf": math.inf,
        "above_one": 1.5,
        "negative": -0.1,
        "bool": True,
    }.items()
}


def noul_mutations(key: str = "noul") -> dict[str, Mutation]:
    return {
        "missing": lambda _: MISSING,
        "wrong_type": lambda answer: answer[key],
        "null": lambda _: None,
        "array": lambda answer: [answer],
        "value_missing": lambda answer: {k: v for k, v in answer.items() if k != key},
        "string": lambda answer: {**answer, key: str(answer[key])},
        "bool": lambda answer: {**answer, key: True},
        "nan": lambda answer: {**answer, key: math.nan},
        "inf": lambda answer: {**answer, key: math.inf},
        "negative": lambda answer: {**answer, key: -0.1},
        "above_max": lambda answer: {**answer, key: 1.1 if key == "noul" else 2.1},
    }


def rubric_mutations() -> dict[str, Mutation]:
    """jev_score answer mutations. No confidence rows: ADR-0043 keeps a malformed confidence as
    `None` beside a valid judgment, and this tool thresholds nothing on it."""
    return {
        "missing": lambda _: MISSING,
        "wrong_type": lambda answer: answer["score"],
        "null": lambda _: None,
        "array": lambda answer: [answer],
        "score_missing": lambda answer: {k: v for k, v in answer.items() if k != "score"},
        "score_not_number": lambda answer: {**answer, "score": "high"},
        "score_nan": lambda answer: {**answer, "score": math.nan},
        "score_inf": lambda answer: {**answer, "score": math.inf},
        "score_negative": lambda answer: {**answer, "score": -0.1},
        "score_above_rubric": lambda answer: {**answer, "score": 1.5},
        "probabilities_missing": lambda answer: {k: v for k, v in answer.items() if k != "probabilities"},
        "probabilities_not_object": lambda answer: {
            **answer,
            "probabilities": list(answer["probabilities"].values()),
        },
        "wrong_keys": lambda answer: {**answer, "probabilities": {"a": 0.5, "b": 0.5}},
        "missing_key": lambda answer: {**answer, "probabilities": {"1": 1.0}},
        "extra_key": lambda answer: {**answer, "probabilities": {"0": 1.0, "1": 0.0, "2": 0.0}},
        "sum_low": lambda answer: {**answer, "probabilities": {"0": 0.78, "1": 0.2}},
        "sum_high": lambda answer: {**answer, "probabilities": {"0": 0.81, "1": 0.21}},
        "value_negative": lambda answer: {**answer, "probabilities": {"0": -0.05, "1": 1.05}},
        "value_above_one": lambda answer: {**answer, "probabilities": {"0": 1.05, "1": -0.05}},
        "value_nan": lambda answer: {**answer, "probabilities": {"0": math.nan, "1": 0.2}},
        "value_string": lambda answer: {**answer, "probabilities": {"0": "0.8", "1": 0.2}},
        "value_bool": lambda answer: {**answer, "probabilities": {"0": True, "1": 0.2}},
    }


@dataclass(frozen=True)
class Case:
    tool: str
    arguments: Mapping[str, Any]
    answers: Mapping[str, Any]
    permissive: Callable[[Any, str], bool]
    """Whether the payload acts permissively on the judgment asked under this answer key."""
    targets: Mapping[str, Mapping[str, Mutation]]
    """Answer key → the mutations applied to it, one at a time."""


def any_action(payload: object, key: str, value: str) -> bool:
    """True if `payload` holds `key: value` anywhere."""
    if isinstance(payload, dict):
        members = cast(dict[str, object], payload)
        return any((k == key and v == value) or any_action(v, key, value) for k, v in members.items())
    if isinstance(payload, list):
        return any(any_action(item, key, value) for item in cast(list[object], payload))
    return False


def auto_anywhere(payload: Any, target: str = "") -> bool:
    return any(any_action(payload, key, "auto") for key in ("action", "decision", "status"))


def judged(payload: Any, target: str = "") -> bool:
    """For tools with no auto tier: the answers were accepted as judgments."""
    return payload.get("status") != "invalid_response"


def decide_judged(payload: Any, target: str) -> bool:
    """jev_decide has no action: a judgment is accepted when it is not reported as invalid_response."""
    if target == "recommendation":
        return payload["recommendation"].get("status") != "invalid_response"
    index = int(target.split("_")[1])
    return payload["checks"][index]["answer"] != "invalid_response"


def choice(chosen: str, probabilities: Mapping[str, float], confidence: float = 0.99) -> dict[str, Any]:
    return {"choice": chosen, "probabilities": dict(probabilities), "confidence": confidence}


SHARED = choice_mutations(0.02)

GOOD_REVIEW: dict[str, Any] = {
    "correctness": {"score": 2, "confidence": 0.95},
    "spec_match": {"score": 2, "confidence": 0.95},
    "test_gap": {"score": 0, "confidence": 0.95},
    "blast_radius": {"score": 0, "confidence": 0.95},
    "safe_to_apply": {"noul": 0.95},
}
REVIEW_TARGETS: dict[str, Mapping[str, Mutation]] = {
    **{rubric: {**noul_mutations("score"), **CONFIDENCE_MUTATIONS} for rubric in list(GOOD_REVIEW)[:4]},
    "safe_to_apply": noul_mutations(),
}

CASES: list[Case] = [
    Case(
        "jev_score",
        {"subject": "Regression risk of the rename.", "levels": ["minor risk", "major risk"]},
        {"grade": {"score": 0.2, "probabilities": {"0": 0.8, "1": 0.2}, "confidence": 0.95}},
        judged,
        {"grade": rubric_mutations()},
    ),
    Case(
        "jev_verify",
        {"claims": ["The service listens on 8080."], "evidence": "server.listen(8080)"},
        {"relation_claim0": choice("supports", {"supports": 0.97, "contradicts": 0.02, "says_nothing": 0.01})},
        auto_anywhere,
        {"relation_claim0": {**SHARED, **CONFIDENCE_MUTATIONS}},
    ),
    Case(
        "jev_screen",
        {"text": "Release notes for 2.1.", "purpose": "Summarize the release."},
        {"injection": {"noul": 0.01}, "substance": {"noul": 0.95}, "relevance": {"noul": 0.9}},
        lambda payload, _: payload["recommendation"]["action"] == "pass",
        {key: noul_mutations() for key in ("injection", "substance", "relevance")},
    ),
    Case(
        "jev_find",
        {"query": "Which port?", "candidates": [{"id": "a", "text": "port 8080"}, {"id": "b", "text": "colors"}]},
        {"best": choice("a", {"a": 0.97, "b": 0.03}), "exists": {"noul": 0.95}},
        judged,
        {"best": SHARED, "exists": noul_mutations()},
    ),
    Case(
        "jev_classify",
        {"items": [{"text": "Refund please"}], "classes": [{"description": "billing"}, {"description": "bug"}]},
        {"i0": choice("c0", {"c0": 0.97, "c1": 0.03})},
        auto_anywhere,
        {"i0": SHARED},
    ),
    Case(
        "jev_rerank",
        {"query": "Which port?", "candidates": [{"text": "port 8080"}, {"text": "colors"}]},
        {"rel_0": {"noul": 0.9}, "rel_1": {"noul": 0.1}},
        judged,
        {"rel_0": noul_mutations(), "rel_1": noul_mutations()},
    ),
    Case(
        "jev_compare",
        {"passage_a": "Price is $5.", "passage_b": "It costs $5.", "aspects": ["price"]},
        {
            "overall": choice("same_fact", {"same_fact": 0.97, "contradicts": 0.02, "different_facts": 0.01}),
            "aspect_0": choice("same_fact", {"same_fact": 0.97, "contradicts": 0.02, "different_facts": 0.01}),
        },
        lambda payload, target: (
            (payload["overall"] if target == "overall" else payload["aspects"][0])["decision"] == "auto"
        ),
        {"overall": SHARED, "aspect_0": SHARED},
    ),
    Case(
        "jev_decide",
        {
            "decision": "Pick a cache.",
            "evidence": "Redis is deployed.",
            "priorities": "Reuse infra.",
            "candidates": [{"id": "redis", "description": "Use Redis"}, {"id": "memcached", "description": "Add it"}],
            "requirements": ["Uses deployed infra"],
        },
        {
            "recommendation": choice(
                "option_0",
                {"option_0": 0.9, "option_1": 0.04, "ask_user": 0.02, "investigate": 0.02, "none": 0.02},
            ),
            "check_0_0": choice("supported", {"supported": 0.9, "contradicted": 0.05, "unknown": 0.05}),
            "check_1_0": choice("contradicted", {"supported": 0.05, "contradicted": 0.9, "unknown": 0.05}),
        },
        decide_judged,
        {"recommendation": SHARED, "check_0_0": SHARED, "check_1_0": SHARED},
    ),
    Case(
        "jev_extract",
        {
            "document": "Build ABC-123 passed. Build ABC-124 failed.",
            "fields": [{"id": "build", "pattern": "[A-Z]{3}-\\d+", "description": "The failed build."}],
        },
        {"f0": choice("c1", {"c0": 0.02, "c1": 0.97, "none_of_them": 0.01})},
        lambda payload, _: payload["results"][0]["status"] in ("auto", "not_found"),
        {"f0": choice_mutations(0.01)},
    ),
    Case(
        "jev_review",
        {"request": "Return 404 for unknown users.", "diff": "+ return res.status(404)"},
        GOOD_REVIEW,
        auto_anywhere,
        REVIEW_TARGETS,
    ),
    Case(
        "jev_gate",
        {
            "request": "Return 404 for unknown users.",
            "diff": "+ return res.status(404)",
            "claims": ["The unknown-user test passes."],
            "evidence": "PASS returns 404 for an unknown id",
        },
        {
            **GOOD_REVIEW,
            "claim_0": choice("verified", {"verified": 0.97, "contradicted": 0.02, "unsupported": 0.01}),
        },
        lambda payload, _: payload["action"] == "auto",
        {**REVIEW_TARGETS, "claim_0": {**SHARED, **CONFIDENCE_MUTATIONS}},
    ),
]

ROWS = [
    pytest.param(case, target, name, mutation, id=f"{case.tool}-{target}-{name}")
    for case in CASES
    for target, mutations in case.targets.items()
    for name, mutation in mutations.items()
]


@pytest.mark.parametrize("case", CASES, ids=[case.tool for case in CASES])
async def test_baseline_is_permissive(case: Case) -> None:
    outcome = await call_tool(case.tool, case.arguments, case.answers)
    assert not outcome.is_error, outcome.text
    for target in case.targets:
        assert case.permissive(outcome.payload, target), outcome.text


@pytest.mark.parametrize(("case", "target", "name", "mutation"), ROWS)
async def test_malformed_answer_is_never_permissive(case: Case, target: str, name: str, mutation: Mutation) -> None:
    answers = dict(case.answers)
    mutated = mutation(answers[target])
    if mutated is MISSING:
        del answers[target]
    else:
        answers[target] = mutated

    outcome = await call_tool(case.tool, case.arguments, answers)

    assert not outcome.is_error, outcome.text
    assert not case.permissive(outcome.payload, target), f"{case.tool} {target} {name}: {outcome.text}"


async def test_extract_invalid_row_has_no_action_key() -> None:
    """Status-only tools fail closed without an Action key (ADR-0042). Extract is the parity hazard."""
    by_tool = {case.tool: case for case in CASES}

    extract = by_tool["jev_extract"]
    answers = dict(extract.answers)
    del answers["f0"]
    outcome = await call_tool(extract.tool, extract.arguments, answers)
    assert not outcome.is_error, outcome.text
    row = outcome.payload["results"][0]
    assert row["status"] == "invalid_response"
    assert row["reason"] is None
    assert "action" not in row

    decide = by_tool["jev_decide"]
    answers = dict(decide.answers)
    del answers["recommendation"]
    outcome = await call_tool(decide.tool, decide.arguments, answers)
    assert not outcome.is_error, outcome.text
    recommendation = outcome.payload["recommendation"]
    assert recommendation["status"] == "invalid_response"
    assert "action" not in recommendation

    for tool_name, answer_key in (("jev_find", "best"), ("jev_rerank", "rel_0")):
        case = by_tool[tool_name]
        answers = dict(case.answers)
        del answers[answer_key]
        outcome = await call_tool(case.tool, case.arguments, answers)
        assert not outcome.is_error, outcome.text
        assert outcome.payload["status"] == "invalid_response"
        assert "action" not in outcome.payload
