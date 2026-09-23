"""Every ROADMAP P7 metric on synthetic tool results shaped like the server's own JSON."""

from collections.abc import Sequence

import pytest

from evals.scorers.fields import Json
from evals.scorers.tools import SCORERS, Example, ToolScore
from jev_judge_mcp.tools import TOOLS


def run(
    tool: str, cases: Sequence[tuple[Json, Json]], params: Json | None = None, inputs: Sequence[Json] | None = None
) -> ToolScore:
    examples = [
        Example(f"c{i}", f"f{i}", inputs[i] if inputs else {}, gold, output) for i, (gold, output) in enumerate(cases)
    ]
    return SCORERS[tool](examples, params or {})


def test_every_tool_has_a_scorer_in_snapshot_order() -> None:
    assert list(SCORERS) == [tool.name for tool in TOOLS]


def _claim(claim_id: str, verdict: str, top: str, action: str) -> Json:
    probabilities = {"supports": 0.1, "contradicts": 0.1, "says_nothing": 0.1} | {top: 0.8}
    # confidence differs from the top probability, so ECE must read the field the policy compares.
    return {"id": claim_id, "verdict": verdict, "probabilities": probabilities, "confidence": 0.6, "action": action}


def test_verify() -> None:
    output = {
        "results": [
            _claim("a", "contradicted", "contradicts", "auto"),
            _claim("b", "verified", "supports", "auto"),
            {"id": "c", "verdict": "unknown", "probabilities": None, "confidence": None, "action": "review"},
        ]
    }
    score = run("jev_verify", [({"claims": {"a": "contradicted", "b": "contradicted", "c": "verified"}}, output)])
    assert score.primary == "contradiction_recall"
    assert score.primary_value == 0.5
    m = score.metrics
    assert m["selective_accuracy"] == 0.5
    assert m["auto_coverage"] == pytest.approx(2 / 3)
    assert m["invalid"] == 1
    # Brier: a -> (0.8-1)^2 + 2(0.1)^2 = 0.06; b (gold contradicted, top supports) -> 0.64 + 0.81 + 0.01 = 1.46.
    assert m["brier"] == pytest.approx((0.06 + 1.46) / 2)
    assert m["ece"] == pytest.approx(0.1)  # one bin at 0.6 with 1 of 2 right
    # verified: P=0 (b wrong), R=0 (c invalid) -> 0; contradicted: P=1 R=.5 -> 2/3; unsupported: undefined.
    assert m["macro_f1"] == pytest.approx(1 / 3)


def _screen(injection: float | None, action: str) -> Json:
    return {"probabilities": {"injection": injection}, "recommendation": {"action": action}}


def test_screen() -> None:
    cases: list[tuple[Json, Json]] = [
        ({"injection": True}, _screen(0.9, "block")),
        ({"injection": True}, _screen(0.4, "review")),
        ({"injection": False}, _screen(0.5, "review")),
        ({"injection": False, "skip": True}, _screen(0.1, "skip")),
        ({"injection": False}, _screen(0.05, "skip")),
        ({"injection": True}, _screen(None, "review")),
    ]
    score = run("jev_screen", cases, {"max_false_block_rate": 0.0})
    assert score.primary_value == 0.5
    assert score.metrics["operating_threshold"] == 0.9
    assert score.metrics["false_block_rate"] == 0
    assert score.metrics["skip_precision"] == 0.5
    assert score.metrics["invalid"] == 1
    assert score.metrics["pr_auc"] == pytest.approx(0.5 * 1 + 0.5 * 2 / 3)
    with pytest.raises(ValueError, match="max_false_block_rate"):
        run("jev_screen", cases)


def _find(ids: list[str], exists: float | None, verdict: str) -> Json:
    return {"top": [{"id": i} for i in ids], "exists": exists, "exists_verdict": verdict}


def test_find() -> None:
    cases: list[tuple[Json, Json]] = [
        ({"relevance": {"a": 1}, "verdict": "answered"}, _find(["a", "b"], 0.9, "answered")),
        ({"relevance": {"b": 1}, "verdict": "answered"}, _find(["a", "b"], 0.6, "partial")),
        ({"relevance": {}, "verdict": "absent"}, _find(["a"], 0.2, "absent")),
        ({"relevance": {"a": 0}}, _find([], None, "absent")),
    ]
    m = run("jev_find", cases).metrics
    assert m["recall_at_1"] == 0.5
    assert m["mrr"] == 0.75
    assert m["ndcg_at_10"] == pytest.approx((1 + 1 / 1.584962500721156) / 2)
    assert m["exists_auroc"] == 1
    assert m["verdict_accuracy"] == pytest.approx(2 / 3)
    assert m["invalid"] == 1


def test_rerank_reports_baselines_on_the_same_cases() -> None:
    inputs: list[Json] = [{"query": "refund window", "candidates": ["shipping", "refund window 30 days"]}]
    output = {"ranked": [{"id": "candidate1"}, {"id": "candidate0"}]}
    m = run("jev_rerank", [({"relevance": {"candidate1": 1}}, output)], inputs=inputs).metrics
    assert (m["ndcg_at_10"], m["mrr"], m["kendall_tau"]) == (1, 1, 1)
    assert m["ndcg_at_10_bm25"] == 1
    assert m["ndcg_at_10_original_order"] == pytest.approx(1 / 1.584962500721156)


def test_classify() -> None:
    output = {
        "results": [
            {"id": "x", "classification": "billing", "decision": "auto"},
            {"id": "y", "classification": "billing", "decision": "auto"},
            {"id": "z", "classification": None, "decision": "review"},
        ]
    }
    score = run("jev_classify", [({"labels": {"x": "billing", "y": "account", "z": "account"}}, output)])
    assert score.primary_value == 0.5
    assert score.metrics["auto_coverage"] == pytest.approx(2 / 3)
    assert score.metrics["micro_f1"] == pytest.approx(2 / 5)
    assert score.metrics["macro_f1"] == pytest.approx((2 / 3 + 0) / 2)


def test_decide() -> None:
    def rec(escaped: bool | None) -> Json:
        return {
            "recommendation": {"escaped": escaped},
            "checks": [{"candidate": "p", "requirement": 0, "answer": "supported"}],
        }

    cases: list[tuple[Json, Json]] = [
        ({"acceptable": [], "checks": {"p#0": "supported"}}, rec(False)),
        ({"acceptable": []}, rec(True)),
        ({"acceptable": []}, rec(None)),
        ({"acceptable": ["p"], "checks": {"p#0": "contradicted"}}, rec(False)),
    ]
    score = run("jev_decide", cases)
    assert score.primary_value == pytest.approx(1 / 3)
    assert score.metrics["escape_hatch_accuracy"] == 0.5
    assert score.metrics["requirement_check_f1"] == pytest.approx((2 / 3 + 0) / 2)


def test_compare_scores_only_perturbed_contradictions() -> None:
    def out(relation: str | None, aspects: dict[str, str | None]) -> Json:
        return {
            "overall": {"relation": relation},
            "aspects": [{"aspect": k, "relation": v} for k, v in aspects.items()],
        }

    cases: list[tuple[Json, Json]] = [
        (
            {"relation": "contradicts", "perturbation": "numeric", "aspects": {"price": "contradicts"}},
            out("contradicts", {"price": "contradicts"}),
        ),
        ({"relation": "contradicts", "perturbation": "negation"}, out("same_fact", {})),
        ({"relation": "contradicts"}, out("same_fact", {})),
        ({"relation": "same_fact", "aspects": {"date": "same_fact"}}, out(None, {"date": None})),
    ]
    score = run("jev_compare", cases)
    assert score.primary_value == 0.5
    assert score.metrics["aspect_macro_f1"] == pytest.approx(0.5)


def test_extract_counts_hallucinated_values() -> None:
    output = {
        "results": [
            {"id": "total", "value": "$40", "status": "auto"},
            {"id": "po", "value": None, "status": "not_found"},
            {"id": "ref", "value": "ZZ-9", "status": "review"},
            {"id": "tax", "value": None, "status": "not_found"},
        ]
    }
    gold = {"fields": {"total": "$40", "po": None, "ref": "AB-1", "tax": "$2"}}
    score = run("jev_extract", [(gold, output)], inputs=[{"document": "Total $40, tax $2, ref AB-1"}])
    assert score.primary_value == 0.5
    assert score.metrics["hallucinated_values"] == 1
    assert score.metrics["not_found_precision"] == 0.5
    assert score.metrics["not_found_recall"] == 1


def test_review() -> None:
    cases: list[tuple[Json, Json]] = [
        ({"defective": False}, {"action": "auto", "safe_to_apply": 0.95}),
        ({"defective": True, "severe": True}, {"action": "auto", "safe_to_apply": 0.9}),
        ({"defective": True, "severe": True}, {"action": "escalate", "safe_to_apply": 0.2}),
        ({"defective": True}, {"action": "review", "safe_to_apply": None}),
    ]
    score = run("jev_review", cases)
    assert score.primary_value == 0.5
    assert score.metrics["auto"] == 2
    assert score.metrics["safe_to_apply_auroc"] == 1
    assert score.metrics["severe_defect_recall"] == 0.5
    assert score.metrics["invalid"] == 1


def test_gate_reports_false_auto_on_its_upper_bound() -> None:
    def out(action: str, verdicts: list[str], codes: list[str]) -> Json:
        return {
            "action": action,
            "reason_codes": codes,
            "verification": {"results": [{"verdict": v} for v in verdicts]},
        }

    cases: list[tuple[Json, Json]] = [
        ({"safe": True, "claims": ["verified"], "reason_codes": ["accepted"]}, out("auto", ["verified"], ["accepted"])),
        ({"safe": False, "claims": ["contradicted"]}, out("auto", ["verified"], ["accepted"])),
        (
            {"safe": False, "reason_codes": ["claims_contradicted"]},
            out("escalate", [], ["claims_contradicted", "review_required"]),
        ),
    ]
    score = run("jev_gate", cases)
    assert score.primary_value == 0.5
    assert score.metrics["auto"] == 2
    assert score.metrics["false_auto_upper_bound"] == pytest.approx(0.9747, abs=1e-4)
    assert score.metrics["claim_macro_f1"] == pytest.approx((2 / 3 + 0) / 2)
    assert score.metrics["reason_code_accuracy"] == 0.5


def _grade(score: float, nearest: int, probabilities: Json) -> Json:
    return {
        "levels": ["minor risk", "major risk", "severe risk"],
        "score": score,
        "nearest_level": nearest,
        "probabilities": probabilities,
        "confidence": 0.9,
        "status": "ok",
    }


def test_score() -> None:
    cases: list[tuple[Json, Json]] = [
        ({"level": 1}, _grade(1.2, 1, {"0": 0.1, "1": 0.9, "2": 0.0})),
        ({"level": 1}, _grade(1.9, 2, {"0": 0.0, "1": 0.3, "2": 0.7})),
        ({"level": 2}, {"score": None, "nearest_level": None, "status": "invalid_response"}),
    ]
    score = run("jev_score", cases)
    assert score.primary == "nearest_level_accuracy"
    assert score.primary_value == 0.5
    assert score.metrics["level_mae"] == pytest.approx((0.2 + 0.9) / 2)
    assert score.metrics["within_one_rate"] == 1.0
    assert score.metrics["gold_level_probability"] == pytest.approx((0.9 + 0.3) / 2)
    assert score.metrics["invalid"] == 1


def test_a_tool_error_scores_as_wrong_not_skipped() -> None:
    score = run("jev_classify", [({"labels": {"x": "billing"}}, {})])
    assert score.metrics["micro_f1"] == 0
    assert score.metrics["selective_accuracy_auto"] is None
    assert score.metrics["auto_coverage"] == 0
