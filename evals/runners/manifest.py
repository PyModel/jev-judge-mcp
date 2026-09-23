"""Manifests, datasets, and recorded outputs: the files an eval run reads.

A manifest (`evals/manifests/*.json`) names one tool, its dataset (relative to `evals/datasets/`), the
pinned Jev model a live run must use, the split salt, and scorer params. A dataset is JSONL rows
`{"id", "family", "input", "gold"}`, where `input` is the tool arguments. Recorded outputs are JSONL
rows `{"id", "repeat", "output"}` (`output` is the parsed tool result, or absent for a tool error).
"""

import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from evals.calibration.split import SPLITS, split_by_family
from evals.scorers.fields import Json, as_object
from evals.scorers.tools import SCORERS, Example

EVALS_DIR = Path(__file__).resolve().parents[1]
DATASETS_DIR = EVALS_DIR / "datasets"
UNPINNED_MODELS = frozenset({"jev-latest"})


@dataclass(frozen=True, slots=True)
class Manifest:
    tool: str
    dataset: Path
    model: str
    salt: str
    params: Json


@dataclass(frozen=True, slots=True)
class Case:
    id: str
    family: str
    input: Json
    gold: Json


def load_manifest(path: Path) -> Manifest:
    raw = as_object(json.loads(path.read_text(encoding="utf-8")))
    tool = str(raw["tool"])
    if tool not in SCORERS:
        raise ValueError(f"{path}: unknown tool {tool!r}")
    model = str(raw["model"])
    if not model or model in UNPINNED_MODELS:
        raise ValueError(f"{path}: model must pin an exact Jev model, not {model!r}")
    dataset = DATASETS_DIR / str(raw["dataset"])
    return Manifest(tool, dataset, model, str(raw.get("salt", "")), as_object(raw.get("params")))


def _jsonl(path: Path) -> list[Json]:
    lines = path.read_text(encoding="utf-8").splitlines()
    return [as_object(json.loads(line)) for line in lines if line.strip()]


def load_cases(path: Path) -> list[Case]:
    rows = _jsonl(path)
    cases = [Case(str(r["id"]), str(r["family"]), as_object(r["input"]), as_object(r["gold"])) for r in rows]
    ids = [case.id for case in cases]
    if len(set(ids)) != len(ids):
        raise ValueError(f"{path}: duplicate case ids")
    return cases


def select_split(cases: Sequence[Case], split: str, salt: str) -> list[Case]:
    """One split's cases, or every case for `all`."""
    if split == "all":
        return list(cases)
    if split not in SPLITS:
        raise ValueError(f"unknown split {split!r}; one of {', '.join((*SPLITS, 'all'))}")
    return split_by_family(cases, lambda case: case.family, salt)[split]


def load_outputs(path: Path) -> dict[str, list[Json]]:
    """Outputs per case id, ordered by `repeat` (default 0); a tool error is an empty output."""
    grouped: dict[str, list[tuple[int, Json]]] = {}
    for row in _jsonl(path):
        repeat = row.get("repeat", 0)
        grouped.setdefault(str(row["id"]), []).append((int(repeat), as_object(row.get("output"))))
    return {
        case_id: [output for _, output in sorted(runs, key=lambda run: run[0])] for case_id, runs in grouped.items()
    }


def examples(cases: Sequence[Case], outputs: dict[str, list[Json]], repeat: int = 0) -> list[Example]:
    """Join cases with their `repeat`-th output. A case with no recorded output is an error, not a skip."""
    missing = [case.id for case in cases if len(outputs.get(case.id, [])) <= repeat]
    if missing:
        raise ValueError(f"no recorded output (repeat {repeat}) for: {', '.join(missing)}")
    return [Example(c.id, c.family, c.input, c.gold, outputs[c.id][repeat]) for c in cases]


def dump_json(value: object) -> str:
    return json.dumps(value, indent=2) + "\n"
