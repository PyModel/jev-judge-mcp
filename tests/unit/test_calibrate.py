"""The calibrate CLI contract (ADR-0070): strict rows, guards, an advisory report, exit codes.

The statistics themselves are owned by `tests/evals/test_calibration.py`; every number asserted
here is hand-derived (zero errors in n rows bound at `1 - 0.05**(1/n)`), never computed by the
modules under test.
"""

import json
from pathlib import Path

import pytest

from jev_judge_mcp import calibrate

# 40 clean rows at one score: the split is 28/12 (30% held out, whole rows as families) and every
# bound is the closed form for zero errors: 1-0.05**(1/28) = 0.101 in selection, 1-0.05**(1/12) = 0.221 held out.
CLEAN = json.dumps({"score": 0.9, "correct": True})


def rows_file(tmp_path: Path, lines: list[str]) -> Path:
    path = tmp_path / "rows.jsonl"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_report_recommends_the_threshold_and_certifies_on_held_out_rows(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = rows_file(tmp_path, [CLEAN] * 40)
    assert calibrate.main([str(path), "--max-error", "0.3"]) == 0
    report = capsys.readouterr().out
    assert "recommended auto_accept: 0.9" in report
    assert "selection: auto 28/28, errors 0, coverage 1.000, upper error bound 0.101" in report
    assert "held-out certification: auto 12/12, errors 0, coverage 1.000, certified upper error bound 0.221" in report
    assert "the held-out bound is within the budget" in report
    assert "never changes the frozen defaults" in report


def test_help_states_the_command_is_advisory_only(capsys: pytest.CaptureFixture[str]) -> None:
    assert calibrate.main(["--help"]) == 0
    out = capsys.readouterr().out
    assert "usage: jev-judge-mcp calibrate" in out
    assert "never changes the frozen defaults in policy/thresholds.py" in out


def test_a_tool_budget_that_no_threshold_meets_exits_one(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    # jev_gate's 0.005 budget: zero errors in 28 rows still bound at 0.101, so nothing qualifies.
    path = rows_file(tmp_path, [CLEAN] * 40)
    assert calibrate.main([str(path), "--tool", "jev_gate"]) == 1
    err = capsys.readouterr().err
    assert "no threshold meets the error budget 0.005 on the 28 selection rows" in err
    # P3-2: the refusal says what the budget demands — ceil(ln(0.05)/ln(0.995)) = 598 zero-error rows.
    assert "a zero-error selection split needs at least 598 rows at this budget" in err


def test_a_failed_held_out_certification_exits_one(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """P2-3: a threshold whose held-out bound exceeds the budget is not a success. Selection bounds
    at 0.254 <= 0.3, held-out errors bound at 0.527 > 0.3 — the report still prints, the exit
    refuses the point."""
    lines = [json.dumps({"score": 0.9, "correct": i not in (2, 5, 8, 11, 14, 17)}) for i in range(40)]
    path = rows_file(tmp_path, lines)
    assert calibrate.main([str(path), "--max-error", "0.3"]) == 1
    out_and_err = capsys.readouterr()
    assert "recommended auto_accept: 0.9" in out_and_err.out
    assert "the held-out certification exceeds the error budget 0.3 (0.527 on 12 rows)" in out_and_err.err


def test_the_report_pairs_review_at_for_tools_that_have_one(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = rows_file(tmp_path, [json.dumps({"score": 0.9, "correct": True, "tool": "jev_verify"})] * 40)
    assert calibrate.main([str(path), "--max-error", "0.3"]) == 0
    report = capsys.readouterr().out
    assert "tool: jev_verify" in report
    # E3: the pairing is policy's own resolve_policy_thresholds, not a re-derived min().
    assert "pair with review_at: 0.5 (policy's own pairing of the pair)" in report


def test_calibrate_is_a_subcommand_and_nothing_else_is() -> None:
    """P3-5: the dispatch predicate matches the `setup` precedent (tests/unit/test_setup.py)."""
    from jev_judge_mcp.server import calibrate_requested

    assert calibrate_requested(["jev-judge-mcp", "calibrate"]) is True
    assert calibrate_requested(["jev-judge-mcp"]) is False
    assert calibrate_requested(["jev-judge-mcp", "install"]) is False
    assert calibrate_requested(["jev-judge-mcp", "--help"]) is False


def test_the_server_slices_calibrate_arguments_like_the_other_subcommands(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """server.main hands calibrate exactly argv[2:]: --help reaches the command, not the server."""
    from jev_judge_mcp import server

    monkeypatch.setattr(server.sys, "argv", ["jev-judge-mcp", "calibrate", "--help"])
    with pytest.raises(SystemExit) as caught:
        server.main()
    assert caught.value.code == 0
    out = capsys.readouterr().out
    assert "usage: jev-judge-mcp calibrate" in out
    assert "never changes the frozen defaults" in out


@pytest.mark.parametrize(
    ("lines", "message"),
    [
        ([CLEAN, "{not json}"], "rows.jsonl:2: not one JSON object"),
        ([CLEAN, '{"score": NaN, "correct": true}'], "rows.jsonl:2: not one JSON object (NaN is not a finite"),
        (['{"correct": true}'], "rows.jsonl:1: need score and correct"),
        (['{"score": true, "correct": true}'], "rows.jsonl:1: score must be a finite number"),
        (['{"score": 1.5, "correct": true}'], "rows.jsonl:1: score 1.5 is outside [0, 1]"),
        (['{"score": 0.9, "correct": "yes"}'], "rows.jsonl:1: correct must be true or false"),
        (['{"score": 0.9, "correct": true, "wrong": 1}'], "rows.jsonl:1: unknown field 'wrong'"),
        (['{"score": 0.9, "correct": true, "family": ""}'], "rows.jsonl:1: family must be a non-empty string"),
        (['{"score": 0.9, "correct": true, "tool": "jev_score"}'], "rows.jsonl:1: unknown tool 'jev_score'"),
        (["[1, 2]"], "rows.jsonl:1: not one JSON object"),
        ([], "rows.jsonl: no rows"),
    ],
    ids=[
        "bad-json",
        "nan-score",
        "missing-score",
        "bool-score",
        "score-range",
        "string-correct",
        "unknown-field",
        "empty-family",
        "unknown-tool",
        "array-line",
        "empty-file",
    ],
)
def test_a_bad_row_stops_the_run_naming_the_line(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], lines: list[str], message: str
) -> None:
    path = rows_file(tmp_path, lines)
    assert calibrate.main([str(path), "--max-error", "0.3"]) == 2
    assert message in capsys.readouterr().err


def test_bad_invocations_exit_two(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = rows_file(tmp_path, [CLEAN] * 40)
    missing = tmp_path / "absent.jsonl"
    cases: list[tuple[list[str], str]] = [
        ([], "need exactly one rows file"),
        ([str(path), str(path)], "need exactly one rows file"),
        ([str(missing)], "could not read the rows file"),
        ([str(path), "--tool"], "--tool needs a value"),
        ([str(path), "--tool", "jev_screen"], "unknown tool 'jev_screen'"),
        ([str(path), "--max-error", "1.5"], "outside (0, 1)"),
        ([str(path), "--max-error", "zero"], "'zero' is not a number"),
        ([str(path), "--min-rows", "1"], "--min-rows must be at least 2"),
        ([str(path), "--wrong"], "unknown option '--wrong'"),
    ]
    for argv, message in cases:
        assert calibrate.main(argv) == 2, argv
        assert message in capsys.readouterr().err, argv


def test_samples_too_small_to_bound_are_refused(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    few = rows_file(tmp_path, [CLEAN] * 10)
    assert calibrate.main([str(few)]) == 2
    assert "10 rows, need at least 40 (--min-rows)" in capsys.readouterr().err
    small = rows_file(tmp_path, [CLEAN] * 10)
    assert calibrate.main([str(small), "--min-rows", "2", "--max-error", "0.5"]) == 2
    # P3-4: ten per-row families fit the 30% target fine, so this is a too-few-rows refusal.
    assert "the held-out split has 3 row(s), need at least 8: add rows" in capsys.readouterr().err


def test_families_too_large_to_hold_out_name_their_own_cause() -> None:
    """P3-4: when every family exceeds the 30% held-out share, the refusal says so instead of
    suggesting family labels that cannot help."""
    rows = [calibrate.ParsedRow(0.9, True, "one", None) for _ in range(20)] + [
        calibrate.ParsedRow(0.9, True, "two", None) for _ in range(20)
    ]
    with pytest.raises(calibrate.CalibrateError, match="every family is larger than the 30%"):
        calibrate.split_rows(rows)


def test_a_budget_must_come_from_tool_or_flag(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = rows_file(tmp_path, [CLEAN] * 40)
    assert calibrate.main([str(path)]) == 2
    assert "no error budget: pass --tool or --max-error" in capsys.readouterr().err
    mixed = rows_file(
        tmp_path,
        [json.dumps({"score": 0.9, "correct": True, "tool": "jev_classify"})] * 20
        + [json.dumps({"score": 0.9, "correct": True, "tool": "jev_verify"})] * 20,
    )
    assert calibrate.main([str(mixed)]) == 2
    assert "rows carry the tools jev_classify, jev_verify; pass --tool to choose one" in capsys.readouterr().err
    clash = rows_file(tmp_path, [json.dumps({"score": 0.9, "correct": True, "tool": "jev_classify"})] * 40)
    assert calibrate.main([str(clash), "--tool", "jev_verify"]) == 2
    assert "a row carries tool 'jev_classify', not --tool 'jev_verify'" in capsys.readouterr().err


def test_the_split_keeps_families_whole_and_is_deterministic() -> None:
    rows = [calibrate.ParsedRow(0.9, True, family, None) for family in (f"f{i}" for i in range(15)) for _ in range(2)]
    first = calibrate.split_rows(rows)
    again = calibrate.split_rows(rows)
    assert first == again
    selection, held_out = first
    assert sorted(row.family for row in selection + held_out) == sorted(row.family for row in rows)
    held_families = {row.family for row in held_out}
    assert held_families.isdisjoint({row.family for row in selection})
    # ceil(0.3 * 30) = 9: four whole 2-row families fit (8 rows); the fifth would overflow.
    assert len(held_out) == 8
    assert len(held_families) == 4


def test_one_oversized_family_stays_in_the_selection_split() -> None:
    rows = [calibrate.ParsedRow(0.9, True, "big", None) for _ in range(40)] + [
        calibrate.ParsedRow(0.9, True, f"r{i}", None) for i in range(20)
    ]
    selection, held_out = calibrate.split_rows(rows)
    # ceil(0.3 * 60) = 18: the 40-row family overflows the target and stays whole in selection;
    # the single-row families fill the held-out split exactly.
    assert len(held_out) == 18
    assert len([row for row in selection if row.family == "big"]) == 40
