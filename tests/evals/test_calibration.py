"""Calibration math: bounds against published intervals, threshold selection, family splits, flips."""

import math
import subprocess
import sys

import pytest

from evals.calibration.bounds import Bound, clopper_pearson_upper, upper_error_bound, wilson_upper
from evals.calibration.flips import flip_rate, is_borderline
from evals.calibration.split import split_by_family
from evals.calibration.targets import PRECISION_TARGETS, error_budget
from evals.calibration.threshold import certify, select_threshold

# Upper ends of two-sided 95% intervals (= one-sided 0.975) for k errors in 10.
PUBLISHED_UPPER = [(0, 0.3085, 0.2775), (1, 0.4450, 0.4042), (5, 0.8129, 0.7634)]


@pytest.mark.parametrize(("errors", "clopper_pearson", "wilson"), PUBLISHED_UPPER)
def test_bounds_match_published_intervals(errors: int, clopper_pearson: float, wilson: float) -> None:
    assert clopper_pearson_upper(errors, 10, 0.975) == pytest.approx(clopper_pearson, abs=5e-5)
    assert wilson_upper(errors, 10, 0.975) == pytest.approx(wilson, abs=5e-5)


def test_zero_errors_follow_the_closed_form() -> None:
    assert clopper_pearson_upper(0, 300) == pytest.approx(1 - 0.05 ** (1 / 300), rel=1e-9)


def test_large_n_does_not_overflow() -> None:
    # Poisson limit: the one-sided 95% upper bound for 5 events is 10.513 per n.
    assert clopper_pearson_upper(5, 100_000) == pytest.approx(10.513e-5, rel=1e-3)


@pytest.mark.parametrize("bound", ["wilson", "clopper_pearson"])
def test_no_evidence_and_all_errors_bound_at_one(bound: Bound) -> None:
    assert upper_error_bound(0, 0, bound) == 1.0
    assert upper_error_bound(7, 7, bound) == 1.0


@pytest.mark.parametrize(("errors", "n", "confidence"), [(-1, 5, 0.95), (6, 5, 0.95), (0, 5, 1.0), (0, 5, 0.0)])
def test_bounds_reject_impossible_inputs(errors: int, n: int, confidence: float) -> None:
    with pytest.raises(ValueError):
        clopper_pearson_upper(errors, n, confidence)
    with pytest.raises(ValueError):
        wilson_upper(errors, n, confidence)


def test_targets_are_error_budgets() -> None:
    assert error_budget("jev_gate") == 0.005
    assert error_budget("jev_classify") == 0.03
    assert set(PRECISION_TARGETS) == {
        "jev_classify",
        "jev_find",
        "jev_compare",
        "jev_extract",
        "jev_verify",
        "jev_review",
        "jev_gate",
    }
    with pytest.raises(KeyError):
        error_budget("jev_screen")


def test_threshold_is_held_to_the_upper_bound_not_the_point_estimate() -> None:
    # 20 correct rows: 0% observed error, but the 95% upper bound (~0.139) exceeds a 3% budget.
    assert select_threshold([(0.9, True)] * 20, max_error=0.03) is None


def test_threshold_maximizes_coverage_over_a_non_monotone_sweep() -> None:
    rows = [(0.99, True)] * 200 + [(0.95, False)] * 3 + [(0.9, True)] * 400
    point = select_threshold(rows, max_error=0.03)
    assert point is not None
    # At 0.95 the three errors hurt; lowering to 0.9 adds 400 correct rows and passes again.
    assert (point.threshold, point.auto, point.errors) == (0.9, 603, 3)
    assert point.coverage == 1.0
    assert point.error_upper_bound <= 0.03


def test_threshold_prefers_the_most_coverage_among_feasible_points() -> None:
    rows = [(0.99, True)] * 300 + [(0.5, False)] * 100
    point = select_threshold(rows, max_error=0.03)
    assert point is not None
    assert (point.threshold, point.auto, point.coverage) == (0.99, 300, 0.75)


def test_threshold_with_no_rows_is_none() -> None:
    assert select_threshold([], max_error=0.5) is None


def test_certify_reports_the_held_out_rows_bound() -> None:
    # The selection rows certify nothing: certify measures only the rows it is given.
    point = select_threshold([(0.99, True)] * 300 + [(0.5, False)] * 100, max_error=0.03)
    assert point is not None
    certified = certify(point, [(0.99, True), (0.99, True), (0.99, False), (0.5, False)])
    assert certified.auto == 3
    assert certified.errors == 1
    assert certified.coverage == 0.75
    assert certified.error_upper_bound == pytest.approx(clopper_pearson_upper(1, 3))


def test_certify_without_evidence_bounds_at_one() -> None:
    point = select_threshold([(0.99, True)] * 300, max_error=0.03)
    assert point is not None
    empty = certify(point, [])
    assert (empty.auto, empty.errors, empty.coverage, empty.error_upper_bound) == (0, 0, 0.0, 1.0)
    all_below = certify(point, [(0.5, True), (0.5, False)])
    assert (all_below.auto, all_below.errors, all_below.error_upper_bound) == (0, 0, 1.0)


def test_split_puts_every_family_in_exactly_one_split() -> None:
    rows = [(family, i) for family in "abcdefghijklmnop" for i in range(ord(family) % 4 + 1)]
    splits = split_by_family(rows, lambda row: row[0])
    families = [{family for family, _ in part} for part in splits.values()]
    assert sum(len(f) for f in families) == len(set[str]().union(*families)) == 16
    assert sorted(r for part in splits.values() for r in part) == sorted(rows)


def test_split_of_equal_families_hits_the_ratios_exactly() -> None:
    splits = split_by_family(list(range(10)), lambda i: f"f{i}")
    assert splits == {"dev": [1, 2, 5, 4, 3, 7], "calibration": [0, 6], "locked_test": [8, 9]}


def test_split_rule_keeps_an_oversized_family_whole() -> None:
    # One family holds 80% of rows. Four singletons hash ahead of it and fill dev's deficit first; the
    # big family still lands whole in dev (the largest deficit), and the rest split evenly.
    rows = ["big"] * 80 + [f"f{i}" for i in range(20)]
    splits = split_by_family(rows, lambda row: row)
    assert {name: len(part) for name, part in splits.items()} == {"dev": 84, "calibration": 8, "locked_test": 8}
    assert splits["dev"].count("big") == 80


def test_split_edge_cases() -> None:
    assert split_by_family(list[str](), lambda row: row) == {"dev": [], "calibration": [], "locked_test": []}
    assert split_by_family(["a", "a"], lambda row: row) == {"dev": ["a", "a"], "calibration": [], "locked_test": []}
    two = split_by_family(["a", "b"], lambda row: row)
    assert two["locked_test"] == [] and sorted(two["dev"] + two["calibration"]) == ["a", "b"]


def test_split_is_stable_across_processes_and_changes_with_the_salt() -> None:
    code = "from evals.calibration.split import split_by_family as s; print(s(list(range(30)), lambda i: str(i)))"
    outputs = {
        subprocess.run(
            [sys.executable, "-c", code], capture_output=True, text=True, check=True, env={"PYTHONHASHSEED": seed}
        ).stdout
        for seed in ("1", "2")
    }
    assert outputs == {str(split_by_family(list(range(30)), lambda i: str(i))) + "\n"}
    assert split_by_family(list(range(30)), str, "v2") != split_by_family(list(range(30)), str)


def test_borderline_is_inclusive_at_the_margin() -> None:
    assert is_borderline(0.85, 0.8)
    assert is_borderline(0.75, 0.8)
    assert not is_borderline(0.8501, 0.8)
    assert not is_borderline(0.7499, 0.8)


def test_flip_rate() -> None:
    assert flip_rate({"a": ["auto"] * 3, "b": ["auto", "review", "auto"]}) == 0.5
    assert flip_rate({}) is None
    with pytest.raises(ValueError, match="need 3-5"):
        flip_rate({"a": ["auto", "auto"]})
    with pytest.raises(ValueError, match="need 3-5"):
        flip_rate({"a": ["auto"] * 6})
    assert math.isclose(flip_rate({"a": ["x", "y", "x", "x", "x"]}) or 0, 1.0)
