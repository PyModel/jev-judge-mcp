"""Generic metric math on hand-computed synthetic cases, including every undefined case."""

import math

import pytest

from evals.baselines.ranking import bm25, embeddings, original_order
from evals.scorers.metrics import (
    accuracy,
    auroc,
    average_precision,
    brier,
    expected_calibration_error,
    f1,
    kendall_tau_b,
    macro_f1,
    mean,
    micro_f1,
    ndcg_at_k,
    precision,
    recall,
    recall_at_false_positive_rate,
    reciprocal_rank,
)


def test_binary_counts() -> None:
    gold = [True, True, False, False]
    predicted = [True, False, True, False]
    assert recall(gold, predicted) == 0.5
    assert precision(gold, predicted) == 0.5
    assert f1(gold, predicted) == 0.5
    assert recall([False], [True]) is None
    assert precision([True], [False]) is None
    assert f1([False], [False]) is None
    assert f1([True], [False]) == 0


def test_multiclass_f1_counts_invalid_predictions_as_wrong() -> None:
    gold = ["a", "a", "b", "c"]
    predicted = ["a", None, "b", "b"]
    # a: P=1 R=.5 F1=2/3; b: P=.5 R=1 F1=2/3; c: F1=0.
    assert macro_f1(gold, predicted, ["a", "b", "c"]) == pytest.approx((2 / 3 + 2 / 3 + 0) / 3)
    # tp=2, fp=1 (b for c), fn=2.
    assert micro_f1(gold, predicted, ["a", "b", "c"]) == pytest.approx(4 / 7)
    assert macro_f1([], [], ["a"]) is None
    assert accuracy(gold, predicted) == 0.5
    assert mean([None, 1.0, 0.0]) == 0.5
    assert mean([None]) is None


def test_brier() -> None:
    rows = [{"a": 1.0, "b": 0.0}, {"a": 0.5, "b": 0.5}]
    assert brier(rows, ["a", "b"], ["a", "b"]) == pytest.approx((0 + 0.5) / 2)
    assert brier([], [], ["a"]) is None


def test_ece_bins_are_right_closed() -> None:
    # 0.3 sits in (0.2, 0.3] despite 0.3 * 10 == 3.0000000000000004; 0.35 sits in (0.3, 0.4].
    assert expected_calibration_error([0.3, 0.35], [True, True]) == pytest.approx(0.5 * 0.7 + 0.5 * 0.65)
    assert expected_calibration_error([0.3, 0.3], [True, False]) == pytest.approx(0.2)
    assert expected_calibration_error([0.0, 1.0], [False, True]) == 0
    assert expected_calibration_error([], []) is None


def test_auroc_uses_mid_ranks_for_ties() -> None:
    assert auroc([0.9, 0.1], [True, False]) == 1
    assert auroc([0.1, 0.9], [True, False]) == 0
    assert auroc([0.5, 0.5], [True, False]) == 0.5
    assert auroc([0.8, 0.5, 0.5, 0.2], [True, True, False, False]) == pytest.approx(0.875)
    assert auroc([0.5, 0.6], [True, True]) is None


def test_average_precision() -> None:
    # Ranked: P, N, P → AP = (1/2)(1) + (1/2)(2/3).
    assert average_precision([0.9, 0.8, 0.7], [True, False, True]) == pytest.approx(0.5 + 1 / 3)
    # A tie between a positive and a negative enters as one threshold at precision 1/2.
    assert average_precision([0.5, 0.5], [True, False]) == 0.5
    assert average_precision([0.5], [False]) is None


def test_recall_at_false_positive_rate_reports_its_operating_point() -> None:
    scores = [0.95, 0.9, 0.8, 0.7, 0.6, 0.1]
    positive = [True, False, True, True, False, False]
    point = recall_at_false_positive_rate(scores, positive, 1 / 3)
    assert (point.threshold, point.recall, point.false_positive_rate) == (0.7, 1.0, pytest.approx(1 / 3))
    strict = recall_at_false_positive_rate(scores, positive, 0.0)
    assert (strict.threshold, strict.recall, strict.false_positive_rate) == (0.95, pytest.approx(1 / 3), 0.0)
    nothing = recall_at_false_positive_rate([0.9, 0.1], [False, True], 0.0)
    assert (nothing.threshold, nothing.recall, nothing.false_positive_rate) == (math.inf, 0.0, 0.0)


def test_ranking_metrics() -> None:
    grades = {"a": 2.0, "b": 1.0, "c": 0.0}
    assert reciprocal_rank(["c", "b", "a"], {"a", "b"}) == 0.5
    assert reciprocal_rank(["c"], {"a"}) == 0
    assert ndcg_at_k(["a", "b", "c"], grades) == 1
    worst = (0 + 1 / math.log2(3) + 3 / 2) / (3 + 1 / math.log2(3))
    assert ndcg_at_k(["c", "b", "a"], grades) == pytest.approx(worst)
    assert ndcg_at_k(["a"], {"a": 0.0}) is None
    assert ndcg_at_k(["c", "a"], grades, k=1) == 0
    assert kendall_tau_b(["a", "b", "c"], grades) == 1
    assert kendall_tau_b(["c", "b", "a"], grades) == -1
    assert kendall_tau_b(["a", "b"], {"a": 1.0, "b": 1.0}) is None
    assert kendall_tau_b(["a"], grades) is None
    assert kendall_tau_b(["b", "x"], {"b": 1.0}) == 1
    # One tied grade pair: tau-b = (2 - 0) / sqrt(3 * 2).
    assert kendall_tau_b(["a", "b", "c"], {"a": 1.0, "b": 0.0, "c": 0.0}) == pytest.approx(2 / math.sqrt(6))


def test_baselines_use_the_tool_candidate_ids() -> None:
    candidates = ["shipping times", {"id": "r", "text": "the refund window is 30 days"}, {"id": "r", "text": "refund"}]
    assert original_order(candidates) == ["candidate0", "r", "r_1"]
    assert bm25("refund window", candidates) == ["r", "r_1", "candidate0"]
    assert bm25("q", []) == []
    with pytest.raises(NotImplementedError):
        embeddings("q", candidates)
    with pytest.raises(TypeError):
        original_order([3])
