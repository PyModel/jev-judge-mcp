"""Adapt the public JevBench items to this repo's eval harness (A8 preparation).

JevBench (`fstandhartinger/jevbench`, MIT) is a third-party benchmark for Jev-class typed decision
models; its published measurement of Jev 1.13.0 is the external anchor this repo currently lacks.
This module READS a JevBench checkout as data — it never executes the harness, whose code is
untrusted here — and converts its public items into this repo's dataset/manifest formats, so the
existing runners and scorers (ADR-0026/0045) do the rest.

Each public `choice` item becomes one jev_classify case: the item's state is the item text, its
labels and criteria map become the class catalog, its instructions become the purpose, and its
`expected` label is the gold. Items are excluded, never distorted: a `noul`/`score` question has
no classify mapping yet, a structured (non-string) state cannot become item text without changing
the task, and a state over the classify item cap (2000 UTF-16 units) would be truncated into a
different question. Every exclusion is counted and reported. The checkout's own manifest pins a
sha256 per public file; a file that does not match its pin refuses the whole conversion.

Not run yet: the paid run waits for its spend decision. The exact command and the measured cost
estimate live in `evals/README.md` § External benchmark.
"""

import argparse
import hashlib
import json
import os
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

from evals.runners.manifest import DATASETS_DIR
from jev_judge_mcp.limits import CLASSIFY
from jev_judge_mcp.text import length as utf16_units
from jev_judge_mcp.tools.arguments import ArgumentsError, compile_argument_schema
from jev_judge_mcp.tools.classify import TOOL as CLASSIFY_TOOL

TIERS = ("easy", "original", "hard")
"""The public item files in `datasets/public/`, as named by JevBench's own manifest."""

PINNED_MODEL = "jev-1.13.0"
DATASET_NAME = "jevbench-public.jsonl"
MANIFEST_NAME = "jevbench-public.json"
SALT = "jevbench-public-v1"
REPORTS_DIR = Path("evals") / "reports" / "jevbench"
"""Default output dir: under the gitignored `evals/reports/`, so tracked dirs stay tracked-only."""

REASON_NON_CHOICE = "non-choice question (noul or score)"
REASON_STRUCTURED_STATE = "structured state (not item text)"
REASON_OVER_CAP = f"state over the classify item cap ({CLASSIFY.item_units} UTF-16 units)"


class ConvertError(RuntimeError):
    """The checkout is not the pinned JevBench data; nothing is written."""


@dataclass(frozen=True, slots=True)
class Item:
    id: str
    family: str
    state: object
    question: dict[str, Any]
    labels: list[str]
    expected: str


@dataclass(slots=True)
class Conversion:
    cases: list[dict[str, Any]] = field(default_factory=list[dict[str, Any]])
    excluded: dict[str, dict[str, int]] = field(default_factory=dict[str, dict[str, int]])

    def exclude(self, tier: str, reason: str) -> None:
        by_reason = self.excluded.setdefault(tier, {})
        by_reason[reason] = by_reason.get(reason, 0) + 1


def _read_pinned(checkout: Path, tier: str, manifest: dict[str, Any]) -> list[Item]:
    """The tier's items, refusing any file that does not match the checkout manifest's sha256."""
    splits = cast("list[dict[str, Any]]", manifest.get("splits", []))
    entry = next((split for split in splits if split.get("name") == tier), None)
    path = checkout / "datasets" / "public" / f"{tier}.jsonl"
    raw = path.read_bytes()
    if entry is None or entry.get("sha256") != hashlib.sha256(raw).hexdigest():
        raise ConvertError(
            f"{path} does not match the checkout manifest's pinned sha256 for {tier!r}"
            " (or the manifest has no entry for the tier)"
        )
    items: list[Item] = []
    for line in raw.decode("utf-8").splitlines():
        if not line.strip():
            continue
        row = cast("dict[str, Any]", json.loads(line))
        items.append(
            Item(
                str(row["id"]),
                str(row["family"]),
                row["state"],
                cast("dict[str, Any]", row["question"] if isinstance(row["question"], dict) else {}),
                [str(label) for label in row["labels"]],
                str(row["expected"]),
            )
        )
    return items


def to_case(tier: str, item: Item) -> tuple[dict[str, Any] | None, str | None]:
    """`(case, None)` for a convertible item, or `(None, exclusion reason)` it is counted under."""
    if item.question.get("type") != "choice":
        return None, REASON_NON_CHOICE
    if not isinstance(item.state, str):
        return None, REASON_STRUCTURED_STATE
    if utf16_units(item.state) > CLASSIFY.item_units:
        return None, REASON_OVER_CAP
    criteria = item.question.get("criteria")
    if not isinstance(criteria, dict) or set(cast("dict[str, Any]", criteria)) != set(item.labels):
        raise ConvertError(f"{tier}/{item.id}: criteria keys do not match labels")
    if item.expected not in item.labels:
        raise ConvertError(f"{tier}/{item.id}: expected {item.expected!r} is not one of the labels")
    return (
        {
            "id": f"{tier}-{item.id}",
            "family": f"{tier}/{item.family}",
            "input": {
                "items": [{"id": "i", "text": item.state}],
                "classes": [{"id": label, "description": f"{label}: {criteria[label]}"} for label in item.labels],
                "purpose": str(item.question.get("instructions", "")),
            },
            "gold": {"labels": {"i": item.expected}},
        },
        None,
    )


def convert(checkout: Path, tiers: Sequence[str] = TIERS) -> Conversion:
    """Every convertible public item as a case, plus the counted exclusion reasons per tier."""
    manifest_path = checkout / "datasets" / "manifest.json"
    try:
        manifest = cast("dict[str, Any]", json.loads(manifest_path.read_text(encoding="utf-8")))
    except (OSError, ValueError) as error:
        raise ConvertError(f"could not read {manifest_path}: {error}") from None
    result = Conversion()
    for tier in tiers:
        for item in _read_pinned(checkout, tier, manifest):
            case, reason = to_case(tier, item)
            if case is None:
                result.exclude(tier, str(reason))
                continue
            result.cases.append(case)
    _pre_validate(result.cases)
    return result


def _pre_validate(cases: list[dict[str, Any]]) -> None:
    """Every converted input must pass jev_classify's argument schema offline, before any spend.

    The sha pin freezes the item files, not their fitness for the tool's caps: a converted case the
    toolset would reject would surface only mid-paid-run. The first violating case id is named.
    """
    parser = compile_argument_schema(
        CLASSIFY_TOOL.name, CLASSIFY_TOOL.definition.input_schema, CLASSIFY_TOOL.refinements
    )
    for case in cases:
        try:
            parser(case["input"])
        except ArgumentsError as error:
            raise ConvertError(f"{case['id']}: fails jev_classify argument validation: {error}") from None


def source_commit(checkout: Path) -> str | None:
    """The checkout's own `HEAD`, recorded in the manifest so a run names its exact upstream."""
    run = subprocess.run(
        ["git", "-C", str(checkout), "rev-parse", "HEAD"],
        check=False,
        capture_output=True,
        text=True,
    )
    return run.stdout.strip() or None if run.returncode == 0 else None


def write(
    conversion: Conversion, datasets_dir: Path, manifests_dir: Path, model: str, commit: str | None
) -> tuple[Path, Path]:
    """The dataset JSONL and its manifest, in the runners' formats (`evals/runners/manifest.py`).

    The manifest's `dataset` is relative to `evals/datasets/` — the path the live runner resolves —
    so the artifacts can live outside it (they default to `evals/reports/jevbench/`).
    """
    datasets_dir.mkdir(parents=True, exist_ok=True)
    manifests_dir.mkdir(parents=True, exist_ok=True)
    dataset = datasets_dir / DATASET_NAME
    with dataset.open("w", encoding="utf-8") as sink:
        for case in conversion.cases:
            sink.write(json.dumps(case) + "\n")
    pinned: dict[str, Any] = {
        "tool": "jev_classify",
        "dataset": os.path.relpath(dataset, DATASETS_DIR),
        "model": model,
        "salt": SALT,
        "params": {},
        "source_commit": commit,
    }
    manifest = manifests_dir / MANIFEST_NAME
    manifest.write_text(json.dumps(pinned, indent=2) + "\n", encoding="utf-8")
    return dataset, manifest


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m evals.external.jevbench", description=__doc__)
    parser.add_argument("checkout", type=Path, help="a JevBench git checkout, read as data only")
    parser.add_argument("--tiers", default=",".join(TIERS), help=f"comma-separated, one of {TIERS}")
    parser.add_argument("--datasets-dir", type=Path, default=REPORTS_DIR)
    parser.add_argument("--manifests-dir", type=Path, default=REPORTS_DIR)
    parser.add_argument("--model", default=PINNED_MODEL, help="the pinned Jev model the manifest records")
    args = parser.parse_args(argv)
    tiers = tuple(tier.strip() for tier in args.tiers.split(",") if tier.strip())
    unknown = [tier for tier in tiers if tier not in TIERS]
    if unknown:
        parser.error(f"unknown tiers {unknown}; one of {TIERS}")
    try:
        conversion = convert(args.checkout, tiers)
        commit = source_commit(args.checkout)
        dataset, manifest = write(conversion, args.datasets_dir, args.manifests_dir, args.model, commit)
    except ConvertError as error:
        sys.stderr.write(f"jevbench: {error}\n")
        return 2
    print(f"{len(conversion.cases)} cases -> {dataset}")
    print(f"manifest -> {manifest} (model {args.model}, source_commit {commit})")
    for tier, reasons in conversion.excluded.items():
        for reason, count in reasons.items():
            print(f"excluded {tier}: {count} x {reason}")
    excluded_total = sum(count for reasons in conversion.excluded.values() for count in reasons.values())
    print(f"{len(conversion.cases)} converted, {excluded_total} excluded (counted, never truncated)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
