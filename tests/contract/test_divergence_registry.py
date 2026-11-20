"""Integrity of `docs/reference/divergences.json` — the single source of divergence truth (ADR-0020).

The registry accounts for every recorded tag and references existing ADRs, tests, and calls.
Its fixture set matches the Python expectation projection in `tests/parity/divergences.py`.
"""

import json
from pathlib import Path

from tests.parity.divergences import DIVERGENT, REGISTRY
from tests.support.fixtures import FIXTURES_DIR, divergences, iter_calls, iter_fixtures

ROOT = Path(__file__).parents[2]

ALL_CALLS = list(iter_calls())
TAGGED = {call.id: tuple(divergences(call)) for call in ALL_CALLS if divergences(call)}
REGISTERED = {fid: entry for entry in REGISTRY["divergences"] for fid in entry["fixtures"]}


def test_enums_are_closed_and_ids_unique() -> None:
    ids = [entry["id"] for entry in REGISTRY["divergences"]]
    assert len(ids) == len(set(ids))
    for entry in REGISTRY["divergences"]:
        assert entry["surface"] in REGISTRY["surface_enum"], entry["id"]
        assert entry["tier"] in REGISTRY["tier_enum"], entry["id"]
        assert entry["status"] in REGISTRY["status_enum"], entry["id"]


def test_required_fields_are_present_and_inhabited() -> None:
    required = {
        "id",
        "surface",
        "tier",
        "status",
        "adr",
        "tests",
        "fixtures",
        "reference_behavior",
        "python_behavior",
    }
    for entry in REGISTRY["divergences"]:
        assert required <= entry.keys(), entry["id"]
        assert entry["reference_behavior"], entry["id"]
        assert entry["python_behavior"], entry["id"]
        assert isinstance(entry["tests"], list) and entry["tests"], entry["id"]


def test_adr_and_pinning_test_paths_exist() -> None:
    for entry in REGISTRY["divergences"]:
        assert (ROOT / entry["adr"]).is_file(), entry["id"]
        for path in entry["tests"]:
            assert (ROOT / path.split("::")[0]).is_file(), (entry["id"], path)


def test_registry_preserves_recorded_tags_and_references_existing_calls() -> None:
    recorded_entries: dict[str, set[str]] = {}
    for entry in REGISTRY["divergences"]:
        if "fixture_tags" in entry:
            for fid in entry["fixtures"]:
                recorded_entries.setdefault(fid, set()).update(entry["fixture_tags"])
    assert TAGGED.keys() == recorded_entries.keys()
    for fid, tags in TAGGED.items():
        assert set(tags) <= recorded_entries[fid], (fid, tags)
    assert REGISTERED.keys() <= {call.id for call in ALL_CALLS}


def test_the_projection_matches_the_registry_exactly() -> None:
    """`DIVERGENT` is derived, never authored: no divergence fact lives in Python code (ADR-0020)."""
    assert set(DIVERGENT) == set(REGISTERED)


def test_recorded_divergences_survive_in_fixtures_and_index() -> None:
    program = next(entry for entry in REGISTRY["divergences"] if entry["id"] == "program-response-fields")
    expected = {call.rsplit("#", 1)[0] for call in program["fixtures"]}
    recorded = {fixture.id: divergences(fixture) for fixture in iter_fixtures()}
    assert {fid for fid, tags in recorded.items() if "ADR-0062" in tags} == expected

    index = json.loads((FIXTURES_DIR / "index.json").read_text(encoding="utf-8"))
    assert {row["id"]: tuple(row["divergences"]) for row in index["fixtures"]} == recorded
