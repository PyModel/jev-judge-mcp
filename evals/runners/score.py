"""Offline scoring: `python -m evals.runners.score MANIFEST OUTPUTS [--split S] [--report PATH]`.

Reads recorded outputs, never a provider. On the calibration split of a calibrated tool the report
also carries the selected AUTO operating point (max coverage with the upper error bound within the
ROADMAP target), the borderline flip rate across repeats, and the point certified on the `locked_test`
split: the gate passes only when that held-out bound is within the budget — the calibration rows the
point was chosen on cannot certify it. The operating point is a report, not a change: frozen defaults
move only through a Sanctioned Divergence ADR.
"""

import argparse
import sys
from collections.abc import Sequence
from dataclasses import asdict
from pathlib import Path

from evals.calibration.flips import MIN_REPEATS, flip_rate, is_borderline
from evals.calibration.rows import CALIBRATED_TOOLS, calibration_rows
from evals.calibration.targets import error_budget
from evals.calibration.threshold import certify, select_threshold
from evals.runners.manifest import (
    Case,
    Manifest,
    dump_json,
    examples,
    load_cases,
    load_manifest,
    load_outputs,
    select_split,
)
from evals.scorers.fields import Json
from evals.scorers.tools import SCORERS


def calibrate(
    tool: str, cases: Sequence[Case], held_out: Sequence[Case], outputs: dict[str, list[Json]]
) -> dict[str, object]:
    """Select the AUTO operating point on `cases` (calibration), then certify it on `held_out` (locked_test).

    Selection reports the calibration rows' bound — the best of its own looks, not evidence. The gate
    passes only when the held-out certification's Clopper-Pearson upper bound is within the budget;
    with no qualifying operating point there is nothing to certify and the gate fails.
    """
    budget = error_budget(tool)
    rows = calibration_rows(tool, examples(cases, outputs))
    point = select_threshold(list(rows.values()), budget)
    report: dict[str, object] = {"max_error": budget, "rows": len(rows), "operating_point": None, "gate": "fail"}
    if point is None:
        return report
    repeats = max(len(outputs[case.id]) for case in cases)
    runs = [
        calibration_rows(tool, examples([c for c in cases if len(outputs[c.id]) > r], outputs, r))
        for r in range(repeats)
    ]
    borderline = [key for key, (score, _) in rows.items() if is_borderline(score, point.threshold)]
    repeated = {
        key: ["auto" if run[key][0] >= point.threshold else "not_auto" for run in runs if key in run]
        for key in borderline
    }
    measured = {key: decisions for key, decisions in repeated.items() if len(decisions) >= MIN_REPEATS}
    certified = certify(point, list(calibration_rows(tool, examples(held_out, outputs)).values()))
    report |= {
        "operating_point": asdict(point),
        "borderline": len(borderline),
        "borderline_unrepeated": len(borderline) - len(measured),
        "flip_rate": flip_rate(measured),
        "certification": {"split": "locked_test", **asdict(certified)},
        "gate": "pass" if certified.error_upper_bound <= budget else "fail",
    }
    return report


def score(manifest: Manifest, outputs_path: Path, split: str) -> dict[str, object]:
    all_cases = load_cases(manifest.dataset)
    cases = select_split(all_cases, split, manifest.salt)
    outputs = load_outputs(outputs_path)
    result = SCORERS[manifest.tool](examples(cases, outputs), manifest.params)
    report: dict[str, object] = {
        "tool": manifest.tool,
        "model": manifest.model,
        "split": split,
        "n": result.n,
        "primary": result.primary,
        "metrics": result.metrics,
    }
    if split == "calibration" and manifest.tool in CALIBRATED_TOOLS:
        held_out = select_split(all_cases, "locked_test", manifest.salt)
        report["calibration"] = calibrate(manifest.tool, cases, held_out, outputs)
    return report


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m evals.runners.score", description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("outputs", type=Path)
    parser.add_argument("--split", default="all")
    parser.add_argument("--report", type=Path, help="write the JSON report here instead of stdout")
    args = parser.parse_args(argv)
    text = dump_json(score(load_manifest(args.manifest), args.outputs, args.split))
    if args.report is None:
        sys.stdout.write(text)
    else:
        args.report.write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
