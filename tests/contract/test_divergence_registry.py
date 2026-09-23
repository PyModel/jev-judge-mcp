"""Integrity of `docs/reference/divergences.json` — the single source of divergence truth (ADR-0020).

The registry must stay closed (enums), complete (every tagged fixture call registered exactly
once), and honest (ADR and pinning-test paths exist, and the Python expectation projection in
`tests/parity/divergences.py` matches its fixture set exactly).
"""

from pathlib import Path

from tests.parity.divergences import DIVERGENT, REGISTRY
from tests.support.fixtures import divergences, iter_calls

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


def test_every_tagged_call_is_registered_exactly_once() -> None:
    assert set(TAGGED) == set(REGISTERED)
    for fid, tags in TAGGED.items():
        entry = REGISTERED[fid]
        assert set(tags) <= set(entry.get("fixture_tags", ())), (fid, tags, entry["id"])


def test_the_projection_matches_the_registry_exactly() -> None:
    """`DIVERGENT` is derived, never authored: no divergence fact lives in Python code (ADR-0020)."""
    assert set(DIVERGENT) == set(REGISTERED)
