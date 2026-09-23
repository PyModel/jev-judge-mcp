"""Bench items: one JSONL row per judgment question, a superset of the eval dataset row.

`evals.runners.manifest.load_cases` reads the same file (`id`, `family`, `input`, `gold`). The bench
adds the question both arms see, the answer vocabulary (`options`, `None` for extract's free value),
the `accept` list of final answers that count as correct, authoring metadata, and the label record.
`intended` is the drafting agent's target answer for the allocation mix, unverified and never a
gold label: it exists only so tests can assert the plan's class mix, labelers label blind to it, and
nothing scores against it. A `draft` row has no gold and no `accept`; only `labeled`
and `frozen` rows carry them, and a paid run needs every row `frozen`.
"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from evals.scorers.fields import Json
from evals.scorers.tools import SCORERS

BENCH150 = Path(__file__).resolve().parents[1] / "datasets" / "bench150" / "items.jsonl"
LABEL_STATUSES = ("draft", "labeled", "frozen")
SOURCE_KINDS = ("fixture", "live-synthetic", "new")
NONE_OPTION = {"jev_find": "none", "jev_decide": "escape"}
"""The answer that means no candidate fits, for the tools whose options are candidate ids."""
NOT_STATED = "not stated"
KEYS = (
    "id",
    "tool",
    "family",
    "input",
    "question",
    "options",
    "gold",
    "accept",
    "intended",
    "perturbation",
    "severity",
    "hard_case",
    "source",
    "label",
)
LABEL_KEYS = ("status", "labelers", "agreed", "adjudicator", "rationale")


@dataclass(frozen=True, slots=True)
class Item:
    id: str
    tool: str
    family: str
    input: Json
    question: str
    options: tuple[str, ...] | None
    gold: Json
    accept: tuple[str, ...]
    intended: str
    perturbation: str | None
    severity: str | None
    hard_case: str | None
    source: Json
    label: Json

    @property
    def status(self) -> str:
        return str(self.label["status"])


def parse_item(row: Json) -> Item:
    """One validated row; a malformed row raises with the item id, never loads half-checked."""
    item_id = str(row.get("id"))
    if tuple(row) != KEYS:
        raise ValueError(f"{item_id}: keys must be {', '.join(KEYS)} in that order, got {', '.join(row)}")
    tool = str(row["tool"])
    if tool not in SCORERS:
        raise ValueError(f"{item_id}: unknown tool {tool!r}")
    options = row["options"]
    if (options is None) != (tool == "jev_extract"):
        raise ValueError(f"{item_id}: options are null exactly for jev_extract")
    label = cast(dict[str, Any], row["label"])
    if tuple(label) != LABEL_KEYS or label["status"] not in LABEL_STATUSES:
        raise ValueError(f"{item_id}: label must be {{{', '.join(LABEL_KEYS)}}} with status in {LABEL_STATUSES}")
    gold, accept = cast(dict[str, Any], row["gold"]), cast(list[str], row["accept"])
    if label["status"] == "draft" and (gold or accept or label["labelers"]):
        raise ValueError(f"{item_id}: a draft row has no gold, no accept, and no labelers")
    if label["status"] != "draft" and not (gold and accept and len(label["labelers"]) >= 2):
        raise ValueError(f"{item_id}: a {label['status']} row needs gold, accept, and two labelers")
    source = cast(dict[str, Any], row["source"])
    if source.get("kind") not in SOURCE_KINDS or (source["kind"] == "new") != (source.get("ref") is None):
        raise ValueError(f"{item_id}: source.kind in {SOURCE_KINDS}; ref is null exactly for new")
    return Item(
        item_id,
        tool,
        str(row["family"]),
        cast(Json, row["input"]),
        str(row["question"]),
        None if options is None else tuple(cast(list[str], options)),
        gold,
        tuple(accept),
        str(row["intended"]),
        row["perturbation"],
        row["severity"],
        row["hard_case"],
        source,
        label,
    )


def load_items(path: Path = BENCH150) -> list[Item]:
    lines = path.read_text(encoding="utf-8").splitlines()
    items = [parse_item(cast(Json, json.loads(line))) for line in lines if line.strip()]
    ids = [item.id for item in items]
    if len(set(ids)) != len(ids):
        raise ValueError(f"{path}: duplicate item ids")
    return items
