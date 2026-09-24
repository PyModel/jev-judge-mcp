"""Runner entry points: offline scoring end to end, and a live runner that refuses without its flag."""

import json
from pathlib import Path
from typing import cast

import pytest

from evals.calibration.bounds import clopper_pearson_upper
from evals.runners import live
from evals.runners.manifest import (
    EVALS_DIR,
    Case,
    Manifest,
    examples,
    load_cases,
    load_manifest,
    load_outputs,
    select_split,
)
from evals.runners.score import calibrate, score
from evals.scorers.fields import Json

SYNTHETIC_MANIFEST = EVALS_DIR / "manifests" / "synthetic-classify.json"
SYNTHETIC_OUTPUTS = EVALS_DIR / "datasets" / "synthetic" / "classify.outputs.jsonl"


def test_synthetic_manifest_scores_offline() -> None:
    report = score(load_manifest(SYNTHETIC_MANIFEST), SYNTHETIC_OUTPUTS, "all")
    assert report["n"] == 10
    assert report["primary"] == "selective_accuracy_auto"
    assert report["metrics"] == {
        "selective_accuracy_auto": 1.0,
        "auto_coverage": 0.8,
        "macro_f1": pytest.approx(0.7685185185185185),
        "micro_f1": pytest.approx(0.8),
    }


def test_splits_partition_the_synthetic_dataset_by_family() -> None:
    manifest = load_manifest(SYNTHETIC_MANIFEST)
    reports = [score(manifest, SYNTHETIC_OUTPUTS, split) for split in ("dev", "calibration", "locked_test")]
    assert [r["n"] for r in reports] == [6, 2, 2]


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> Path:
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    return path


def _manifest(tmp_path: Path, tool: str = "jev_classify", model: str = "jev-pinned") -> Path:
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps({"tool": tool, "dataset": str(tmp_path / "cases.jsonl"), "model": model}))
    return path


def test_calibration_split_reports_an_operating_point_and_borderline_flips(tmp_path: Path) -> None:
    # One case per family. Every 40th case is wrong at 0.6; every 10th (offset 1) is right at 0.96 but
    # reads 0.9 on its second repeat; the rest are right at 0.99. Each case runs 3 times.
    cases: list[dict[str, object]] = []
    outputs: list[dict[str, object]] = []
    for i in range(1000):
        wrong, wobbly = i % 40 == 0, i % 10 == 1
        cases.append({"id": f"k{i}", "family": f"fam{i}", "input": {}, "gold": {"labels": {"t": "a"}}})
        for repeat in range(3):
            top = 0.6 if wrong else 0.9 if wobbly and repeat == 1 else 0.96 if wobbly else 0.99
            result = {"id": "t", "classification": "b" if wrong else "a", "top_probability": top}
            outputs.append({"id": f"k{i}", "repeat": repeat, "output": {"results": [result]}})
    manifest = load_manifest(_manifest(tmp_path))
    _write_jsonl(tmp_path / "cases.jsonl", cases)
    calibration_ids = {c.id for c in select_split(load_cases(manifest.dataset), "calibration", manifest.salt)}
    right = [i for i in range(1000) if f"k{i}" in calibration_ids and i % 40 != 0]
    wobbly = [i for i in right if i % 10 == 1]
    assert wobbly and len(right) < len(calibration_ids)  # the split holds both flips and wrong rows
    locked_ids = {c.id for c in select_split(load_cases(manifest.dataset), "locked_test", manifest.salt)}
    right_locked = [i for i in range(1000) if f"k{i}" in locked_ids and i % 40 != 0]
    assert right_locked  # the held-out split holds rows the selected point accepts

    report = score(manifest, _write_jsonl(tmp_path / "out.jsonl", outputs), "calibration")

    assert report["calibration"] == {
        "max_error": 0.03,
        "rows": len(calibration_ids),
        # 0.6 would admit the wrong rows; 0.96 is the lowest threshold whose upper bound fits 3%.
        "operating_point": {
            "threshold": 0.96,
            "auto": len(right),
            "errors": 0,
            "coverage": len(right) / len(calibration_ids),
            "error_upper_bound": pytest.approx(clopper_pearson_upper(0, len(right))),
        },
        # 0.99 and 0.96 both sit within 0.05 of 0.96; only the wobbly cases flip.
        "borderline": len(right),
        "borderline_unrepeated": 0,
        "flip_rate": pytest.approx(len(wobbly) / len(right)),
        # The point is certified on locked_test, never on the calibration rows that chose it.
        "certification": {
            "split": "locked_test",
            "auto": len(right_locked),
            "errors": 0,
            "coverage": len(right_locked) / len(locked_ids),
            "error_upper_bound": pytest.approx(clopper_pearson_upper(0, len(right_locked))),
        },
        "gate": "pass",
    }


def test_a_point_within_budget_on_calibration_fails_the_gate_on_locked_test() -> None:
    # 100 calibration rows with zero errors select 0.99 (upper bound ~2.95% <= the 3% budget); the
    # held-out split's two wrong AUTO rows bound far above it, so the gate fails on locked_test alone.
    calibration = [Case(f"c{i}", f"cf{i}", {}, {"labels": {"t": "a"}}) for i in range(100)]
    locked = [Case(f"t{i}", f"tf{i}", {}, {"labels": {"t": "a"}}) for i in range(20)]
    wrong = {f"t{i}" for i in range(2)}

    def output(case: Case) -> Json:
        return {
            "results": [{"id": "t", "classification": "a" if case.id not in wrong else "b", "top_probability": 0.99}]
        }

    outputs = {case.id: [output(case)] for case in (*calibration, *locked)}

    report = calibrate("jev_classify", calibration, locked, outputs)

    point = cast(dict[str, object], report["operating_point"])
    assert cast(float, point["error_upper_bound"]) <= cast(float, report["max_error"])
    certification = cast(dict[str, object], report["certification"])
    assert certification["split"] == "locked_test"
    assert (certification["auto"], certification["errors"]) == (20, 2)
    assert cast(float, certification["error_upper_bound"]) == pytest.approx(clopper_pearson_upper(2, 20))
    assert cast(float, certification["error_upper_bound"]) > cast(float, report["max_error"])
    assert report["gate"] == "fail"


def test_manifest_must_pin_a_model_and_name_a_tool(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="pin an exact Jev model"):
        load_manifest(_manifest(tmp_path, model="jev-latest"))
    with pytest.raises(ValueError, match="unknown tool"):
        load_manifest(_manifest(tmp_path, tool="jev_nope"))


def test_eval_runtime_provider_disables_retries(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The eval runtime's provider runs with retries off (ADR-0057), so one tool call is at most one
    bounded request.

    No network: constructing the provider builds no client. The fixture key never leaves the redactor.
    """
    from jev_judge_mcp.providers import NO_RETRIES
    from jev_judge_mcp.providers.typesafe import TypeSafeProvider
    from jev_judge_mcp.settings import Settings

    monkeypatch.setenv("JEV_PROVIDER", "typesafe")
    monkeypatch.setenv("TYPESAFE_API_KEY", "eval-fixture-key")
    monkeypatch.setenv("JEV_MCP_KEY_FILE", str(tmp_path / "absent-key"))

    eval_provider = cast(TypeSafeProvider, live.typesafe_without_retries(Settings()))

    assert eval_provider._retry == NO_RETRIES  # pyright: ignore[reportPrivateUsage]


def test_eval_runtime_provider_refuses_without_a_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """No env key and no stored key: the factory refuses exactly like the server's resolver, before
    the runtime could start a run. The temp HOME and absent key file prove no stored key is picked up.
    """
    from jev_judge_mcp.providers.base import ProviderConfigError
    from jev_judge_mcp.settings import Settings

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / ".config"))
    monkeypatch.setenv("JEV_MCP_KEY_FILE", str(tmp_path / "absent-key"))
    monkeypatch.setenv("JEV_PROVIDER", "typesafe")
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)

    with pytest.raises(ProviderConfigError, match=r"^JEV_PROVIDER=typesafe but TYPESAFE_API_KEY is not set\.$"):
        live.typesafe_without_retries(Settings())


def test_a_case_without_a_recorded_output_is_an_error(tmp_path: Path) -> None:
    row: dict[str, object] = {"id": "a", "family": "f", "input": {}, "gold": {}}
    cases = load_cases(_write_jsonl(tmp_path / "cases.jsonl", [row]))
    with pytest.raises(ValueError, match="no recorded output"):
        examples(cases, load_outputs(_write_jsonl(tmp_path / "out.jsonl", [])))


def test_duplicate_case_ids_are_rejected(tmp_path: Path) -> None:
    row: dict[str, object] = {"id": "a", "family": "f", "input": {}, "gold": {}}
    with pytest.raises(ValueError, match="duplicate"):
        load_cases(_write_jsonl(tmp_path / "cases.jsonl", [row, row]))


@pytest.mark.parametrize("environ", [{}, {"JEV_EVAL_LIVE": "0"}, {"JEV_EVAL_LIVE": "true"}])
def test_live_runner_refuses_without_the_flag(environ: dict[str, str], capsys: pytest.CaptureFixture[str]) -> None:
    # The manifest path does not exist: refusal comes before any file, settings, or provider is touched.
    assert live.main(["/nonexistent/manifest.json", "/nonexistent/out.jsonl"], environ) == 2
    assert "refused: live evals call a paid provider; set JEV_EVAL_LIVE=1" in capsys.readouterr().err
    with pytest.raises(live.LiveRunRefusedError):
        live.require_live_enabled(environ)


def test_live_runner_requires_the_pinned_model() -> None:
    manifest = Manifest("jev_classify", Path("unused"), "jev-pinned", "", {})
    live.require_pinned_model(manifest, "jev-pinned")
    with pytest.raises(live.LiveRunRefusedError, match="not the pinned"):
        live.require_pinned_model(manifest, "jev-latest")


@pytest.mark.parametrize("provider", ["auto", "openrouter", "compatible"])
def test_live_runner_refuses_any_provider_but_typesafe(
    provider: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # The dataset does not exist: refusal comes before any case is loaded or provider is built.
    monkeypatch.setenv("JEV_PROVIDER", provider)
    assert live.main([str(_manifest(tmp_path)), str(tmp_path / "out.jsonl")], {live.LIVE_FLAG: "1"}) == 2
    assert f"refused: JEV_PROVIDER={provider!r}; live evals run only against 'typesafe'" in capsys.readouterr().err
    assert not (tmp_path / "out.jsonl").exists()
    live.require_typesafe("typesafe")
    live.require_typesafe("TypeSafe")


def test_the_request_cap_refuses_before_any_request() -> None:
    live.require_within_cap(5, 5)
    with pytest.raises(live.LiveRunRefusedError, match="request cap"):
        live.require_within_cap(live.LIVE_REQUEST_CAP + 1, 1)
