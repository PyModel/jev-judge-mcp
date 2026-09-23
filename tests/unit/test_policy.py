"""Policy engine (ROADMAP P3): the reference `test/unit.test.mjs` policy cases plus every branch."""

import math

import pytest

from jev_judge_mcp.policy import (
    DEFAULT_AUTO_ACCEPT,
    EXTRACT_REASON_CODES,
    GATE_REASON_CODES,
    REVIEW_WEIGHTS,
    THRESHOLD_INVARIANT_MESSAGE,
    Action,
    ClaimJudgment,
    ExtractFieldDecision,
    ExtractFieldEvidence,
    ExtractJudgment,
    PolicyThresholds,
    RequirementCheck,
    ScreenRecommendation,
    ThresholdInvariant,
    claim_action,
    classification_decision,
    contradicts_recommendation,
    decide_extract_field,
    exists_verdict,
    fail_closed,
    gate_reason_codes,
    min_confidence,
    rank_candidates,
    require_complete_context,
    rerank_by_score,
    resolve_policy_thresholds,
    review_action,
    review_composite,
    screen_fail_closed,
    screen_recommendation,
    validate_policy_thresholds,
    verify_action,
    worst_action,
)

# ── thresholds ───────────────────────────────────────────────────────────────


def test_resolve_policy_thresholds_fills_and_validates_the_pair() -> None:
    assert resolve_policy_thresholds(0.8) == PolicyThresholds(auto_accept=0.8, review_at=0.5)
    # A lone low auto_accept cannot invert the pair.
    assert resolve_policy_thresholds(0.3) == PolicyThresholds(auto_accept=0.3, review_at=0.3)
    assert resolve_policy_thresholds(0.9, 0.6) == PolicyThresholds(auto_accept=0.9, review_at=0.6)
    assert resolve_policy_thresholds() == PolicyThresholds(auto_accept=DEFAULT_AUTO_ACCEPT, review_at=0.5)


def test_explicit_zero_review_at_is_not_omitted() -> None:
    assert resolve_policy_thresholds(0.8, 0) == PolicyThresholds(auto_accept=0.8, review_at=0)
    assert resolve_policy_thresholds(0, 0) == PolicyThresholds(auto_accept=0, review_at=0)


@pytest.mark.parametrize(
    ("auto_accept", "review_at"),
    [
        (0.5, 0.8),
        (1.2, None),
        (-0.1, None),
        (0.8, -0.1),
        (0.8, 1.1),
        (math.nan, None),
        (math.inf, None),
        (0.8, math.nan),
        (0.8, -math.inf),
    ],
)
def test_resolve_policy_thresholds_rejects_violations(auto_accept: float, review_at: float | None) -> None:
    assert resolve_policy_thresholds(auto_accept, review_at) == ThresholdInvariant()


def test_threshold_message_is_the_reference_text() -> None:
    assert THRESHOLD_INVARIANT_MESSAGE == "Thresholds must satisfy 0 <= review_at <= auto_accept <= 1."
    assert ThresholdInvariant().message == THRESHOLD_INVARIANT_MESSAGE
    assert validate_policy_thresholds(1, 1) is None
    assert validate_policy_thresholds(0, 0) is None
    assert validate_policy_thresholds(-0.0, -0.0) is None
    assert validate_policy_thresholds(0.5, 0.8) == ThresholdInvariant()


# ── actions ──────────────────────────────────────────────────────────────────


def test_verify_action_gates_on_auto_accept() -> None:
    assert verify_action(0.8, 0.8) == "auto"
    assert verify_action(0.79, 0.8) == "review"
    assert verify_action(0.99, 0.8) == "auto"
    assert verify_action(math.nan, 0) == "review"


def test_fail_closed_token_by_family() -> None:
    """Action tools escalate, decision tools review, and the status-only four have no Action."""
    assert fail_closed("review") == "escalate"
    assert fail_closed("gate") == "escalate"
    assert fail_closed("verify") == "review"
    assert fail_closed("classify") == "review"
    assert fail_closed("compare") == "review"
    assert fail_closed("extract") == "status"
    assert fail_closed("decide") == "status"
    assert fail_closed("find") == "status"
    assert fail_closed("rerank") == "status"


def test_classification_decision_requires_top_probability_and_margin() -> None:
    assert classification_decision(0.9, 0.6, 0.85, 0.5) == "auto"
    assert classification_decision(0.9, 0.4, 0.85, 0.5) == "review"  # high top, thin margin
    assert classification_decision(0.8, 0.8, 0.85, 0.5) == "review"  # wide margin, low top
    assert classification_decision(0.85, 0.5, 0.85, 0.5) == "auto"  # exactly at both gates


def test_require_complete_context_demotes_only_auto() -> None:
    assert require_complete_context("auto", True) == "review"
    assert require_complete_context("auto", False) == "auto"
    assert require_complete_context("escalate", True) == "escalate"
    assert require_complete_context("review", True) == "review"
    assert require_complete_context("review", False) == "review"


def test_worst_action_picks_the_most_severe() -> None:
    assert worst_action(["auto", "review"]) == "review"
    assert worst_action(["auto", "review", "escalate"]) == "escalate"
    assert worst_action(["auto"]) == "auto"
    assert worst_action([]) == "auto"
    one_pass: list[Action] = ["review", "auto"]
    assert worst_action(iter(one_pass)) == "review"


# ── review ───────────────────────────────────────────────────────────────────


def test_review_composite_weights_and_inverts() -> None:
    perfect = review_composite(correctness=2, spec_match=2, test_gap=0, blast_radius=0)
    assert perfect == 1
    assert review_composite(correctness=0, spec_match=0, test_gap=2, blast_radius=2) == 0
    # Out-of-range scores are clamped, never extrapolated.
    assert review_composite(correctness=9, spec_match=3, test_gap=-1, blast_radius=-5) == perfect
    # Good tests and a tiny blast radius contribute fully, so zero them to isolate correctness.
    assert review_composite(correctness=2, spec_match=0, test_gap=2, blast_radius=2) == 0.4


def test_review_composite_is_the_left_to_right_float_sum() -> None:
    # JS adds left to right: 0.26249999999999996. Python's compensated sum() would give 0.2625.
    assert review_composite(correctness=0, spec_match=0.5, test_gap=0, blast_radius=1.5) == 0.26249999999999996


def test_review_composite_keeps_js_min_max_semantics() -> None:
    # Math.max(0, NaN) is NaN in JS; Python's max would return 0.
    assert math.isnan(review_composite(correctness=math.nan, spec_match=2, test_gap=0, blast_radius=0))
    # Math.max(0, -0) is +0, so a negative zero never leaks into the composite.
    assert math.copysign(1, review_composite(correctness=-0.0, spec_match=-0.0, test_gap=2, blast_radius=2)) == 1


def test_review_weights_are_the_reference_weights_in_order() -> None:
    assert list(REVIEW_WEIGHTS.items()) == [
        ("correctness", 0.4),
        ("spec_match", 0.3),
        ("test_gap", 0.15),
        ("blast_radius", 0.15),
    ]


def _review(
    *,
    composite: float = 0.9,
    safe_to_apply: float = 0.95,
    min_confidence: float | None = 0.9,
    auto_accept: float = 0.8,
    review_at: float = 0.5,
    composite_floor: float = 0.7,
) -> Action:
    return review_action(
        composite=composite,
        safe_to_apply=safe_to_apply,
        min_confidence=min_confidence,
        auto_accept=auto_accept,
        review_at=review_at,
        composite_floor=composite_floor,
    )


def test_review_action_gates_auto() -> None:
    assert _review() == "auto"
    assert _review(composite=0.5) == "review"  # below the floor reviews, not escalates
    assert _review(composite=0.75, composite_floor=0.8) == "review"
    assert _review(safe_to_apply=0.4) == "escalate"
    assert _review(min_confidence=0.2) == "escalate"
    assert _review(safe_to_apply=0.6) == "review"
    assert _review(min_confidence=0.6) == "review"


def test_review_action_treats_unknown_confidence_as_escalate_even_at_zero_thresholds() -> None:
    assert _review(min_confidence=None, auto_accept=0, review_at=0, composite_floor=0) == "escalate"
    assert _review(min_confidence=None) == "escalate"


def test_min_confidence_is_unknown_when_any_rubric_is_unknown() -> None:
    assert min_confidence([0.9, 0.4, 0.7, 0.8]) == 0.4
    assert min_confidence([0.9, None, 0.7, 0.8]) is None
    assert min_confidence([None]) is None
    assert min_confidence([]) == math.inf  # Math.min() of nothing


# ── claims and gate ──────────────────────────────────────────────────────────


def test_claim_action_escalates_low_confidence_and_confident_contradictions() -> None:
    assert claim_action("verified", 0.95, 0.8, 0.5) == "auto"
    assert claim_action("verified", 0.6, 0.8, 0.5) == "review"
    assert claim_action("verified", 0.3, 0.8, 0.5) == "escalate"
    assert claim_action("contradicted", 0.95, 0.8, 0.5) == "escalate"
    assert claim_action("contradicted", 0.6, 0.8, 0.5) == "review"
    assert claim_action("unsupported", 0.95, 0.8, 0.5) == "review"


def test_claim_action_treats_unknown_confidence_as_escalate_even_at_zero_thresholds() -> None:
    assert claim_action("verified", None, 0, 0) == "escalate"
    assert claim_action("verified", None, 0.8, 0.5) == "escalate"


DEFAULT = PolicyThresholds(auto_accept=0.8, review_at=0.5)


def _codes(
    *,
    truncated: bool = False,
    review_action: Action = "auto",
    review_invalid: bool = False,
    claims: list[ClaimJudgment | None] | None = None,
    action: Action = "auto",
    thresholds: PolicyThresholds = DEFAULT,
) -> list[str]:
    return gate_reason_codes(
        truncated=truncated,
        review_action=review_action,
        review_invalid=review_invalid,
        claims=claims if claims is not None else [ClaimJudgment("verified", 0.9)],
        action=action,
        thresholds=thresholds,
    )


def test_gate_reason_codes_accepted() -> None:
    assert _codes() == ["accepted"]
    assert _codes(claims=[]) == ["accepted"]


def test_gate_reason_codes_every_code_in_frozen_order() -> None:
    codes = _codes(
        truncated=True,
        review_action="escalate",
        review_invalid=True,
        claims=[
            None,
            ClaimJudgment("contradicted", 0.9),
            ClaimJudgment("unsupported", 0.2),
            ClaimJudgment("verified", 0.6),
        ],
        action="escalate",
    )
    assert codes == [
        "incomplete_context",
        "invalid_response",
        "review_escalated",
        "claims_contradicted",
        "claims_unsupported",
        "claim_confidence_low",
        "claim_confidence_below_auto_accept",
    ]
    assert _codes(review_action="review", action="review") == ["review_required"]


def test_gate_reason_codes_invalid_from_either_half() -> None:
    assert _codes(review_invalid=True, action="escalate") == ["invalid_response"]
    assert _codes(claims=[None], action="escalate") == ["invalid_response"]


def test_gate_unknown_claim_confidence_reports_low_even_at_zero_thresholds() -> None:
    zero = PolicyThresholds(auto_accept=0, review_at=0)
    claims: list[ClaimJudgment | None] = [ClaimJudgment("verified", None)]
    assert _codes(claims=claims, action="escalate", thresholds=zero) == ["claim_confidence_low"]


def test_gate_confidence_boundaries() -> None:
    at_review = [ClaimJudgment("verified", 0.5)]
    assert _codes(claims=list(at_review), action="review") == ["claim_confidence_below_auto_accept"]
    at_auto: list[ClaimJudgment | None] = [ClaimJudgment("verified", 0.8)]
    assert _codes(claims=at_auto) == ["accepted"]


def test_gate_reason_code_vocabulary_is_frozen() -> None:
    assert GATE_REASON_CODES == (
        "incomplete_context",
        "invalid_response",
        "review_escalated",
        "review_required",
        "claims_contradicted",
        "claims_unsupported",
        "claim_confidence_low",
        "claim_confidence_below_auto_accept",
        "accepted",
    )


def test_contradicts_recommendation_flags_only_the_recommended_candidate() -> None:
    checks = [
        RequirementCheck("a", 0, "contradicted"),
        RequirementCheck("a", 1, "supported"),
        RequirementCheck("b", 0, "contradicted"),
        RequirementCheck("a", 2, "unknown"),
        RequirementCheck("a", 3, "contradicted"),
    ]
    assert contradicts_recommendation(checks, "a") == [0, 3]
    assert contradicts_recommendation(checks, "b") == [0]
    assert contradicts_recommendation([], "a") == []


# ── ranking ──────────────────────────────────────────────────────────────────


def test_exists_verdict_uses_cookbook_thresholds() -> None:
    assert exists_verdict(0.98) == "answered"
    assert exists_verdict(0.7) == "answered"
    assert exists_verdict(0.46) == "partial"
    assert exists_verdict(0.35) == "partial"
    assert exists_verdict(0.14) == "absent"
    assert exists_verdict(0.5, found=0.5, absent=0.1) == "answered"


def test_rank_candidates_orders_by_probability_and_keeps_caller_order_on_ties() -> None:
    candidates = [{"id": "a", "text": "1"}, {"id": "b", "text": "2"}, {"id": "c", "text": "3"}]
    ranked = rank_candidates(candidates, {"a": 0.1, "b": 0.5, "c": 0.1})
    assert [c["id"] for c in ranked] == ["b", "a", "c"]
    assert ranked[0] == {"id": "b", "text": "2", "probability": 0.5}
    assert rank_candidates([{"id": "x", "text": "1"}], {})[0]["probability"] == 0
    assert candidates[0] == {"id": "a", "text": "1"}  # inputs are not mutated


def test_rank_candidates_overwrites_probability_in_place() -> None:
    # `{...candidate, probability}` keeps an existing key at its position.
    ranked = rank_candidates([{"probability": 9, "id": "a"}], {"a": 0.25})
    assert list(ranked[0].items()) == [("probability", 0.25), ("id", "a")]


def test_rerank_by_score_sorts_by_index_aligned_relevance() -> None:
    candidates = [{"id": "a"}, {"id": "b"}, {"id": "c"}]
    ranked = rerank_by_score(candidates, [0.2, 0.9, 0.5])
    assert [c["id"] for c in ranked] == ["b", "c", "a"]
    assert ranked[0]["relevance"] == 0.9
    assert [c["id"] for c in rerank_by_score(candidates, [0.5, 0.5, 0.5])] == ["a", "b", "c"]
    # Fewer scores than candidates is a wiring mistake; the missing one ranks at 0.
    short = rerank_by_score(candidates, [0.2, 0.9])
    assert [(c["id"], c["relevance"]) for c in short] == [("b", 0.9), ("a", 0.2), ("c", 0)]


# ── screen ───────────────────────────────────────────────────────────────────


def test_screen_escalates_by_injection_then_demotes_junk() -> None:
    def action(**kwargs: float) -> str:
        return screen_recommendation(block_at=0.75, review_at=0.25, **kwargs).action

    assert action(injection=0.9) == "block"
    assert action(injection=0.4) == "review"
    assert action(injection=0.01) == "pass"
    assert action(injection=0.01, substance=0.1) == "skip"
    assert action(injection=0.01, relevance=0.05) == "skip"
    assert action(injection=0.01, substance=0.3, relevance=0.3) == "pass"


def test_screen_reason_strings() -> None:
    assert screen_recommendation(injection=0.125, block_at=0.1, review_at=0.05) == ScreenRecommendation(
        "block", "injection probability 0.13 >= block threshold 0.1"
    )
    assert screen_recommendation(injection=0.4, block_at=0.75, review_at=0.25) == ScreenRecommendation(
        "review", "injection probability 0.40 >= review threshold 0.25"
    )
    assert screen_recommendation(injection=0.01, substance=0.1, relevance=0.05, block_at=0.75, review_at=0.25) == (
        ScreenRecommendation("skip", "little substantive content (substance 0.10)")
    )
    assert screen_recommendation(injection=0.01, relevance=0.005, block_at=0.75, review_at=0.25) == (
        ScreenRecommendation("skip", "not relevant to the stated purpose (relevance 0.01)")
    )
    assert screen_recommendation(injection=0, block_at=0.75, review_at=0.25) == ScreenRecommendation(
        "pass", "no signals above thresholds"
    )


def test_screen_fails_closed_to_review() -> None:
    """A missing or malformed answer is not a clean bill of health."""
    assert screen_fail_closed() == ScreenRecommendation("review", "missing or malformed answers; cannot screen safely")


def test_screen_thresholds_print_as_js_numbers() -> None:
    # `${blockAt}` is Number::toString: 1e-7, never Python's 1e-07; 1 not 1.0.
    assert screen_recommendation(injection=0.5, block_at=1e-7, review_at=0).reason == (
        "injection probability 0.50 >= block threshold 1e-7"
    )
    assert screen_recommendation(injection=1, block_at=1.0, review_at=0).reason == (
        "injection probability 1.00 >= block threshold 1"
    )


# ── extract ──────────────────────────────────────────────────────────────────


def _extract(evidence: ExtractFieldEvidence) -> ExtractFieldDecision:
    return decide_extract_field(evidence, threshold=0.85, margin=0.5)


def test_extract_field_without_candidates_is_not_found_unless_matches_were_skipped() -> None:
    assert _extract(ExtractFieldEvidence(0, False, None)) == ExtractFieldDecision("not_found", "no_regex_matches")
    assert _extract(ExtractFieldEvidence(1, False, None)) == ExtractFieldDecision("review", "matches_too_long")


def test_extract_truncated_universe_without_a_kept_candidate_is_never_a_definite_not_found() -> None:
    """An executor that capped a universe while keeping no candidate owes `review`, not `not_found`.

    Production `match_all` never yields `truncated` with empty candidates, but the executor seam
    (ADR-0016) permits an adapter that does; the module's own invariant must hold for it too.
    """
    assert _extract(ExtractFieldEvidence(0, True, None)) == ExtractFieldDecision("review", "candidate_limit")


@pytest.mark.parametrize(
    ("top", "gap", "expected"),
    [
        (0.9, 0.6, ExtractFieldDecision("auto", None)),
        (0.85, 0.5, ExtractFieldDecision("auto", None)),  # exactly at both gates
        (0.9, 0.4, ExtractFieldDecision("review", None)),  # thin margin
        (0.8, 0.8, ExtractFieldDecision("review", None)),  # low top
    ],
)
def test_extract_pick_is_gated_on_top_probability_and_margin(top: float, gap: float, expected: object) -> None:
    assert _extract(ExtractFieldEvidence(0, False, ExtractJudgment(False, top, gap))) == expected


def test_extract_none_matched_is_gated_like_a_pick() -> None:
    confident = ExtractJudgment(True, 0.9, 0.8)
    assert _extract(ExtractFieldEvidence(0, False, confident)) == ExtractFieldDecision("not_found", "none_matched")
    ambiguous = ExtractJudgment(True, 0.6, 0.2)
    assert _extract(ExtractFieldEvidence(0, False, ambiguous)) == ExtractFieldDecision(
        "review", "none_matched_ambiguous"
    )


@pytest.mark.parametrize(("too_long", "truncated"), [(1, False), (0, True), (2, True)])
@pytest.mark.parametrize("none_matched", [False, True])
def test_extract_incomplete_universe_is_candidate_limit_review(
    too_long: int, truncated: bool, none_matched: bool
) -> None:
    confident = ExtractJudgment(none_matched, 1.0, 1.0)
    decision = decide_extract_field(ExtractFieldEvidence(too_long, truncated, confident), threshold=0, margin=0)
    assert decision == ExtractFieldDecision("review", "candidate_limit")


def test_extract_reason_codes_are_the_closed_set() -> None:
    assert EXTRACT_REASON_CODES == (
        "no_regex_matches",
        "matches_too_long",
        "candidate_limit",
        "none_matched",
        "none_matched_ambiguous",
    )
