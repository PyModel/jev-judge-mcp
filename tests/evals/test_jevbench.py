"""The JevBench adapter (A8): pinned reads, faithful mapping, counted exclusions.

The fixture builds a minimal JevBench-shaped checkout in tmp_path — its manifest pins the sha256 of
exactly the files it ships — then proves the converted cases are valid jev_classify arguments (the
real toolset validates them) and score correctly against the gold labels through the standard
scorer. Nothing from the real JevBench repository is executed or read here.
"""

import hashlib
import json
from pathlib import Path
from typing import Any, cast

import pytest

from evals.external import jevbench
from evals.runners.manifest import Manifest
from evals.runners.score import score
from tests.support.jev import call_tool

CHOICE = {"type": "choice", "instructions": "Which intent does the user's message express?"}
CRITERIA = {
    "track_order": "Wants to know where an order is",
    "cancel_order": "Wants to cancel an order",
}


def item(name: str, state: object, question: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": name,
        "family": "intent",
        "state": state,
        "question": question,
        "labels": ["track_order", "cancel_order"],
        "expected": "cancel_order" if name.endswith("2") else "track_order",
        "split": "public",
    }


ITEMS = [
    item("keep-choice", "Where is my package?", CHOICE | {"criteria": CRITERIA}),
    item("keep-choice-2", "Please cancel order 71.", CHOICE | {"criteria": CRITERIA}),
    item("drop-noul", "Is the sky blue?", {"type": "noul", "instructions": "Is it true?"}),
    item("drop-structured", {"conversation": []}, CHOICE | {"criteria": CRITERIA}),
    item("drop-over-cap", "x" * 2001, CHOICE | {"criteria": CRITERIA}),
]


def write_tier(checkout: Path, tier: str, rows: list[dict[str, Any]]) -> None:
    """Write one public tier and refresh the checkout manifest's sha256 pin for it."""
    raw = ("\n".join(json.dumps(row) for row in rows) + "\n").encode()
    (checkout / "datasets" / "public" / f"{tier}.jsonl").write_bytes(raw)
    manifest_path = checkout / "datasets" / "manifest.json"
    loaded: object = json.loads(manifest_path.read_text()) if manifest_path.exists() else {"splits": []}
    manifest = cast("dict[str, Any]", loaded)
    splits = cast("list[dict[str, Any]]", manifest["splits"])
    manifest["splits"] = [split for split in splits if split["name"] != tier]
    manifest["splits"].append({"name": tier, "n": len(rows), "sha256": hashlib.sha256(raw).hexdigest()})
    manifest_path.write_text(json.dumps(manifest))


def fixture_checkout(tmp_path: Path) -> Path:
    (tmp_path / "datasets" / "public").mkdir(parents=True)
    for tier in jevbench.TIERS:
        write_tier(tmp_path, tier, ITEMS)
    return tmp_path


def test_convert_keeps_choice_items_and_counts_every_exclusion(tmp_path: Path) -> None:
    conversion = jevbench.convert(fixture_checkout(tmp_path))
    assert [case["id"] for case in conversion.cases] == [
        f"{tier}-{name}" for tier in jevbench.TIERS for name in ("keep-choice", "keep-choice-2")
    ]
    excluded = {jevbench.REASON_NON_CHOICE: 1, jevbench.REASON_STRUCTURED_STATE: 1, jevbench.REASON_OVER_CAP: 1}
    assert conversion.excluded == {tier: excluded for tier in jevbench.TIERS}
    first = conversion.cases[0]
    assert first["family"] == "easy/intent"
    assert first["input"]["items"] == [{"id": "i", "text": "Where is my package?"}]
    assert first["input"]["purpose"] == CHOICE["instructions"]
    assert first["input"]["classes"] == [
        {"id": "track_order", "description": "track_order: Wants to know where an order is"},
        {"id": "cancel_order", "description": "cancel_order: Wants to cancel an order"},
    ]
    assert first["gold"] == {"labels": {"i": "track_order"}}


@pytest.mark.anyio
async def test_converted_cases_pass_the_real_tool_and_score_against_gold(tmp_path: Path) -> None:
    """The owner-boundary proof: the real toolset accepts each converted input (no is_error), the
    result row names the chosen class by its label id, and the standard scorer reads the answers
    back against the gold labels — five of six right, so micro_f1 5/6."""
    checkout = fixture_checkout(tmp_path)
    conversion = jevbench.convert(checkout)
    dataset, manifest_path = jevbench.write(
        conversion, tmp_path / "datasets", tmp_path / "manifests", jevbench.PINNED_MODEL
    )

    outputs = tmp_path / "outputs.jsonl"
    with outputs.open("w", encoding="utf-8") as sink:
        for index, case in enumerate(conversion.cases):
            labels = [cls["id"] for cls in case["input"]["classes"]]
            gold_index = labels.index(case["gold"]["labels"]["i"])
            chosen = gold_index if index != 1 else (gold_index + 1) % len(labels)  # case 1 answers wrong
            probabilities = {f"c{k}": (0.9 if k == chosen else 0.1) for k in range(len(labels))}
            answers = {"i0": {"choice": f"c{chosen}", "probabilities": probabilities, "confidence": 0.9}}
            outcome = await call_tool("jev_classify", case["input"], answers)
            assert not outcome.is_error, outcome.text
            assert outcome.payload["results"][0]["decision"] == "auto"
            assert outcome.payload["results"][0]["classification"] == labels[chosen]
            sink.write(json.dumps({"id": case["id"], "repeat": 0, "output": outcome.payload}) + "\n")

    manifest = Manifest("jev_classify", dataset, jevbench.PINNED_MODEL, jevbench.SALT, {})
    report = score(manifest, outputs, "all")
    metrics = cast("dict[str, object]", report["metrics"])
    assert cast("float", metrics["micro_f1"]) == pytest.approx(5 / 6)  # six items, one wrong
    assert cast("float", metrics["auto_coverage"]) == pytest.approx(1.0)
    assert manifest_path.read_text().count("jev_classify") == 1


def test_a_tampered_file_is_refused_by_its_sha_pin(tmp_path: Path) -> None:
    checkout = fixture_checkout(tmp_path)
    path = checkout / "datasets" / "public" / "easy.jsonl"
    path.write_bytes(path.read_bytes().replace(b"Where is my package?", b"Where is my parcel??"))
    with pytest.raises(jevbench.ConvertError, match="pinned sha256"):
        jevbench.convert(checkout)


def test_criteria_that_do_not_match_labels_refuse_the_conversion(tmp_path: Path) -> None:
    checkout = fixture_checkout(tmp_path)
    broken = [dict(row) for row in ITEMS]
    broken[0]["question"] = CHOICE | {"criteria": {"typo": "…", "cancel_order": "Wants to cancel an order"}}
    write_tier(checkout, "easy", broken)
    with pytest.raises(jevbench.ConvertError, match="criteria keys do not match labels"):
        jevbench.convert(checkout)


def test_the_cli_writes_dataset_and_manifest_and_reports_exclusions(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    checkout = fixture_checkout(tmp_path)
    datasets, manifests = tmp_path / "out-datasets", tmp_path / "out-manifests"
    assert jevbench.main([str(checkout), "--datasets-dir", str(datasets), "--manifests-dir", str(manifests)]) == 0
    out = capsys.readouterr().out
    assert "6 cases" in out
    assert "9 excluded" in out
    assert f"excluded easy: 1 x {jevbench.REASON_NON_CHOICE}" in out
    rows = [json.loads(line) for line in (datasets / jevbench.DATASET_NAME).read_text().splitlines()]
    assert len(rows) == 6
    manifest = cast("dict[str, Any]", json.loads((manifests / jevbench.MANIFEST_NAME).read_text()))
    assert manifest == {
        "tool": "jev_classify",
        "dataset": jevbench.DATASET_NAME,
        "model": jevbench.PINNED_MODEL,
        "salt": jevbench.SALT,
        "params": {},
    }


def test_an_unknown_tier_is_a_usage_error(tmp_path: Path) -> None:
    checkout = fixture_checkout(tmp_path)
    with pytest.raises(SystemExit) as caught:
        jevbench.main([str(checkout), "--tiers", "secret"])
    assert caught.value.code == 2
