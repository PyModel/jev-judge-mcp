"""Hypothesis properties for the policy engine (ROADMAP P3).

Truncated context never yields auto; unknown confidence never yields auto, even at zero thresholds;
worst_action is monotone; an incomplete extract universe is never auto or not_found.
"""

from hypothesis import assume, given
from hypothesis import strategies as st

from jev_judge_mcp.policy import (
    EXTRACT_REASON_CODES,
    GATE_REASON_CODES,
    Action,
    ClaimJudgment,
    ClaimVerdict,
    ExtractFieldEvidence,
    ExtractJudgment,
    PolicyThresholds,
    claim_action,
    decide_extract_field,
    gate_reason_codes,
    min_confidence,
    rank_candidates,
    require_complete_context,
    rerank_by_score,
    review_action,
    review_composite,
    worst_action,
)

SEVERITY: dict[Action, int] = {"auto": 0, "review": 1, "escalate": 2}
ACTIONS = st.sampled_from(["auto", "review", "escalate"])
VERDICTS = st.sampled_from(["verified", "contradicted", "unsupported"])
UNIT = st.floats(min_value=0, max_value=1)
CONFIDENCE = st.one_of(st.none(), UNIT)


@st.composite
def thresholds(draw: st.DrawFn) -> PolicyThresholds:
    """Any valid pair, zero thresholds included."""
    auto_accept = draw(UNIT)
    return PolicyThresholds(auto_accept=auto_accept, review_at=draw(st.floats(min_value=0, max_value=auto_accept)))


# ── truncated ⇒ never auto ───────────────────────────────────────────────────


@given(ACTIONS)
def test_truncated_context_never_auto(action: Action) -> None:
    assert require_complete_context(action, True) != "auto"
    assert SEVERITY[require_complete_context(action, True)] >= SEVERITY[action]
    assert require_complete_context(action, False) == action


@given(VERDICTS, CONFIDENCE, thresholds())
def test_truncated_claim_never_auto(verdict: ClaimVerdict, confidence: float | None, t: PolicyThresholds) -> None:
    assert require_complete_context(claim_action(verdict, confidence, t.auto_accept, t.review_at), True) != "auto"


@given(UNIT, UNIT, st.lists(CONFIDENCE, min_size=4, max_size=4), UNIT, thresholds())
def test_truncated_review_never_auto(
    composite: float, safe: float, confidences: list[float | None], floor: float, t: PolicyThresholds
) -> None:
    action = review_action(
        composite=composite,
        safe_to_apply=safe,
        min_confidence=min_confidence(confidences),
        auto_accept=t.auto_accept,
        review_at=t.review_at,
        composite_floor=floor,
    )
    assert require_complete_context(action, True) != "auto"


# ── unknown confidence ⇒ never auto ──────────────────────────────────────────


@given(VERDICTS, thresholds())
def test_unknown_claim_confidence_never_auto(verdict: ClaimVerdict, t: PolicyThresholds) -> None:
    assert claim_action(verdict, None, t.auto_accept, t.review_at) == "escalate"
    assert claim_action(verdict, None, 0, 0) == "escalate"


@given(UNIT, UNIT, UNIT, st.lists(CONFIDENCE, min_size=4, max_size=4), UNIT, thresholds())
def test_unknown_rubric_confidence_never_auto(
    composite: float, safe: float, unknown_at: float, confidences: list[float | None], floor: float, t: PolicyThresholds
) -> None:
    confidences[int(unknown_at * 3.999)] = None
    for auto_accept, review_at, composite_floor in ((t.auto_accept, t.review_at, floor), (0, 0, 0)):
        action = review_action(
            composite=composite,
            safe_to_apply=safe,
            min_confidence=min_confidence(confidences),
            auto_accept=auto_accept,
            review_at=review_at,
            composite_floor=composite_floor,
        )
        assert action == "escalate"


@given(st.lists(st.one_of(st.none(), st.tuples(VERDICTS, CONFIDENCE)), min_size=1, max_size=8), thresholds())
def test_unknown_claim_confidence_is_reported_low(
    raw: list[tuple[ClaimVerdict, float | None] | None], t: PolicyThresholds
) -> None:
    claims = [None if item is None else ClaimJudgment(*item) for item in raw]
    actions: list[Action] = [
        "escalate" if c is None else claim_action(c.verdict, c.confidence, t.auto_accept, t.review_at) for c in claims
    ]
    action = worst_action(actions)
    codes = gate_reason_codes(
        truncated=False, review_action="auto", review_invalid=False, claims=claims, action=action, thresholds=t
    )
    if any(c is not None and c.confidence is None for c in claims):
        assert "claim_confidence_low" in codes
        assert action != "auto"
    # Reason codes are a subsequence of the frozen order, and accepted means exactly auto.
    order = [GATE_REASON_CODES.index(code) for code in codes]
    assert order == sorted(set(order))
    assert ("accepted" in codes) == (action == "auto")


# ── worst_action is monotone ─────────────────────────────────────────────────


@given(st.lists(ACTIONS, max_size=10))
def test_worst_action_is_the_max_severity(actions: list[Action]) -> None:
    assert SEVERITY[worst_action(actions)] == max((SEVERITY[a] for a in actions), default=0)


@given(st.lists(ACTIONS, max_size=10), st.lists(ACTIONS, max_size=10))
def test_worst_action_never_improves_when_actions_are_added(xs: list[Action], ys: list[Action]) -> None:
    assert SEVERITY[worst_action(xs + ys)] >= SEVERITY[worst_action(xs)]
    assert worst_action(xs + ys) == worst_action(ys + xs)


@given(st.lists(ACTIONS, min_size=1, max_size=10), st.integers(min_value=0), ACTIONS)
def test_worst_action_is_monotone_per_element(actions: list[Action], at: int, replacement: Action) -> None:
    i = at % len(actions)
    if SEVERITY[replacement] >= SEVERITY[actions[i]]:
        worse: list[Action] = [*actions[:i], replacement, *actions[i + 1 :]]
        assert SEVERITY[worst_action(worse)] >= SEVERITY[worst_action(actions)]


# ── composite and ranking ────────────────────────────────────────────────────

SCORE = st.floats(min_value=0, max_value=2)


@given(SCORE, SCORE, SCORE, SCORE, SCORE)
def test_review_composite_is_bounded_and_monotone(c: float, s: float, t: float, b: float, higher: float) -> None:
    composite = review_composite(correctness=c, spec_match=s, test_gap=t, blast_radius=b)
    assert 0 <= composite <= 1 + 1e-12
    raised = review_composite(correctness=max(c, higher), spec_match=s, test_gap=t, blast_radius=b)
    worse_tests = review_composite(correctness=c, spec_match=s, test_gap=max(t, higher), blast_radius=b)
    assert raised >= composite - 1e-12
    assert worse_tests <= composite + 1e-12


@given(st.lists(UNIT, max_size=20))
def test_rank_candidates_is_a_stable_descending_permutation(probabilities: list[float]) -> None:
    candidates = [{"id": f"c{i}"} for i in range(len(probabilities))]
    ranked = rank_candidates(candidates, {f"c{i}": p for i, p in enumerate(probabilities)})
    keys = [(-float(str(r["probability"])), int(str(r["id"])[1:])) for r in ranked]
    assert keys == sorted(keys)
    assert sorted(str(r["id"]) for r in ranked) == sorted(str(c["id"]) for c in candidates)


@given(st.lists(UNIT, max_size=20))
def test_rerank_by_score_is_a_stable_descending_permutation(scores: list[float]) -> None:
    ranked = rerank_by_score([{"i": i} for i in range(len(scores))], scores)
    keys = [(-float(str(r["relevance"])), int(str(r["i"]))) for r in ranked]
    assert keys == sorted(keys)
    assert len(ranked) == len(scores)


@given(
    too_long=st.integers(0, 3),
    truncated=st.booleans(),
    judgment=st.one_of(st.none(), st.builds(ExtractJudgment, st.booleans(), UNIT, UNIT)),
    threshold=UNIT,
    margin=UNIT,
)
def test_extract_incomplete_universe_is_never_final(
    too_long: int, truncated: bool, judgment: ExtractJudgment | None, threshold: float, margin: float
) -> None:
    assume(judgment is not None or not truncated)  # a capped universe has candidates
    decision = decide_extract_field(
        ExtractFieldEvidence(too_long, truncated, judgment), threshold=threshold, margin=margin
    )
    assert decision.reason is None or decision.reason in EXTRACT_REASON_CODES
    if decision.status == "auto":
        assert decision.reason is None
    if truncated or too_long:
        assert decision.status not in ("auto", "not_found")
