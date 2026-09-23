"""Policy parity: every recorded decision recomputes from the recorded judgments (ROADMAP P3, ADR-0002).

Each recorded tool output carries the validated judgments next to the reference's actions. Running
those judgments back through `jev_judge_mcp.policy` must reproduce every action, verdict, composite,
reason string, reason code, and ranking the reference emitted. Rows the tool marked
`invalid_response` never reached policy and are skipped here. Fixture calls come from
`tests.support.fixtures`, the one loader (ADR-0015).
"""

import json
import re
from collections import Counter
from typing import Any

import pytest

from jev_judge_mcp.policy import (
    Action,
    ClaimJudgment,
    ExtractFieldDecision,
    ExtractFieldEvidence,
    ExtractJudgment,
    RequirementCheck,
    claim_action,
    classification_decision,
    contradicts_recommendation,
    decide_extract_field,
    exists_verdict,
    gate_reason_codes,
    min_confidence,
    require_complete_context,
    rerank_by_score,
    review_action,
    review_composite,
    screen_recommendation,
    verify_action,
    worst_action,
)
from jev_judge_mcp.policy.thresholds import PolicyThresholds
from jev_judge_mcp.serialize import stringify
from jev_judge_mcp.validation import margin
from tests.support.fixtures import FixtureCall, iter_calls

TOOLS = (
    "jev_gate",
    "jev_review",
    "jev_screen",
    "jev_find",
    "jev_verify",
    "jev_classify",
    "jev_rerank",
    "jev_extract",
    "jev_compare",
    "jev_decide",
)

type Output = dict[str, Any]


def recorded() -> list[tuple[FixtureCall, Output, Output, Output]]:
    """(call, tool arguments, parsed output, first provider answers) per successful call of a `TOOLS` tool."""
    cases: list[tuple[FixtureCall, Output, Output, Output]] = []
    for call in iter_calls():
        result: Output = call.payload["result"]
        if call.payload["tool"] in TOOLS and result.get("content") and not result.get("isError"):
            output: Output = json.loads(result["content"][0]["text"])
            exchanges = call.payload["exchanges"]
            answers: Output = json.loads(exchanges[0]["response"]["body"]).get("answers", {}) if exchanges else {}
            cases.append((call, call.payload["arguments"], output, answers))
    return cases


CASES = recorded()


def test_every_ported_tool_has_recorded_decisions() -> None:
    counts = Counter(output["tool"] for _, _, output, _ in CASES)
    assert set(counts) == set(TOOLS)


def _review_half(review: Output, truncated: bool) -> Action:
    """Recompute the review half; returns its action."""
    if review.get("status") == "invalid_response":
        assert review["action"] == "escalate"
        assert review["composite"] is None
        return "escalate"
    scores = review["scores"]
    composite = review_composite(
        correctness=scores["correctness"]["score"],
        spec_match=scores["spec_match"]["score"],
        test_gap=scores["test_gap"]["score"],
        blast_radius=scores["blast_radius"]["score"],
    )
    assert stringify(composite) == stringify(review["composite"])
    t = review["thresholds"]
    action = require_complete_context(
        review_action(
            composite=composite,
            safe_to_apply=review["safe_to_apply"],
            min_confidence=min_confidence(scores[k]["confidence"] for k in scores),
            auto_accept=t["auto_accept"],
            review_at=t["review_at"],
            composite_floor=t["composite_floor"],
        ),
        truncated,
    )
    assert action == review["action"]
    return action


def _check_gate(output: Output) -> None:
    truncated: bool = output["truncated"]
    review: Output = output["review"]
    review_act = _review_half(review, truncated)
    verification: Output = output["verification"]
    t = PolicyThresholds(**verification["thresholds"])
    claims: list[ClaimJudgment | None] = []
    for row in verification["results"]:
        if row.get("status") == "invalid_response":
            assert row["action"] == "escalate"
            claims.append(None)
            continue
        claims.append(ClaimJudgment(row["verdict"], row["confidence"]))
        action = claim_action(row["verdict"], row["confidence"], t.auto_accept, t.review_at)
        assert require_complete_context(action, truncated) == row["action"]
    assert worst_action(row["action"] for row in verification["results"]) == verification["action"]
    action = worst_action([review_act, verification["action"]])
    assert action == output["action"]
    codes = gate_reason_codes(
        truncated=truncated,
        review_action=review_act,
        review_invalid=review.get("status") == "invalid_response",
        claims=claims,
        action=action,
        thresholds=t,
    )
    assert codes == output["reason_codes"]


def _check_screen(output: Output) -> None:
    probabilities = output["probabilities"]
    t = output["thresholds"]
    recommendation = screen_recommendation(
        injection=probabilities["injection"],
        substance=probabilities["substance"],
        relevance=probabilities["relevance"],
        block_at=t["block_at"],
        review_at=t["review_at"],
    )
    assert {"action": recommendation.action, "reason": recommendation.reason} == output["recommendation"]


def _check_verify(output: Output) -> None:
    for row in output["results"]:
        if row.get("status") != "invalid_response":
            confidence = row["confidence"]
            expected = "review" if confidence is None else verify_action(confidence, output["auto_accept"])
            assert expected == row["action"]


def _check_classify(output: Output) -> None:
    t = output["thresholds"]
    for row in output["results"]:
        if row.get("status") != "invalid_response":
            decision = classification_decision(
                row["top_probability"], row["margin"], t["auto_accept"], t["minimum_margin"]
            )
            assert decision == row["decision"]


def _check_extract(output: Output) -> None:
    t = output["thresholds"]
    for row in output["results"]:
        if row["status"] in ("invalid_pattern", "invalid_response"):
            continue
        judgment = (
            ExtractJudgment(row["value"] is None, row["top_probability"], row["margin"])
            if "top_probability" in row
            else None
        )
        evidence = ExtractFieldEvidence(row["matches_skipped_too_long"], row["candidates_truncated"], judgment)
        decision = decide_extract_field(evidence, threshold=t["auto_accept"], margin=t["minimum_margin"])
        assert decision == ExtractFieldDecision(row["status"], row["reason"])


def _check_compare(output: Output) -> None:
    t = output["thresholds"]
    for judgment in [output["overall"], *output["aspects"]]:
        if judgment.get("status") == "invalid_response":
            continue
        probabilities = judgment["probabilities"]
        gap = margin(probabilities)
        assert stringify(gap) == stringify(judgment["margin"])
        top = probabilities[judgment["relation"]]
        assert classification_decision(top, gap, t["auto_accept"], t["minimum_margin"]) == judgment["decision"]


WARNING = re.compile(r"Requirements? ([\d, ]+) contradicted by the recommended candidate; inspect before acting")


def _check_decide(output: Output) -> None:
    # Only a pick of a real candidate is checked against the requirements; an escape hatch never is.
    recommendation = output["recommendation"]
    contradicted: list[int] = []
    if recommendation.get("status") != "invalid_response" and not recommendation["escaped"]:
        checks = [
            RequirementCheck(c["candidate"], c["requirement"], c["answer"])
            for c in output["checks"]
            if c["answer"] != "invalid_response"
        ]
        contradicted = contradicts_recommendation(checks, recommendation["selected"])
    warned = [int(n) - 1 for w in output["warnings"] if (m := WARNING.fullmatch(w)) for n in m[1].split(", ")]
    assert len(output["warnings"]) == (1 if contradicted else 0)
    assert warned == contradicted


def _check_rerank(arguments: Output, output: Output, answers: Output) -> None:
    # Scores are the recorded rel_i Nouls in caller order; top_k keeps a prefix of the full ranking.
    candidates = [{"text": c["text"]} for c in arguments["candidates"]]
    ranked = rerank_by_score(candidates, [answers[f"rel_{i}"]["noul"] for i in range(len(candidates))])
    returned = [(row["text"], row["relevance"]) for row in output["ranked"]]
    assert [(r["text"], r["relevance"]) for r in ranked][: len(returned)] == returned


@pytest.mark.parametrize(("call", "arguments", "output", "answers"), CASES, ids=[case[0].test_id for case in CASES])
def test_policy_reproduces_recorded_decision(
    call: FixtureCall, arguments: Output, output: Output, answers: Output
) -> None:
    if output.get("status") == "invalid_response":
        return
    tool = output["tool"]
    if tool == "jev_gate":
        _check_gate(output)
    elif tool == "jev_review":
        _review_half(output, output["truncated"])
    elif tool == "jev_screen":
        _check_screen(output)
    elif tool == "jev_find":
        assert exists_verdict(output["exists"]) == output["exists_verdict"], call.id
    elif tool == "jev_verify":
        _check_verify(output)
    elif tool == "jev_classify":
        _check_classify(output)
    elif tool == "jev_extract":
        _check_extract(output)
    elif tool == "jev_compare":
        _check_compare(output)
    elif tool == "jev_decide":
        _check_decide(output)
    elif tool == "jev_rerank":
        _check_rerank(arguments, output, answers)
    else:
        raise AssertionError(f"no policy recompute for {tool}")
