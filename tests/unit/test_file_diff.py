"""A file list is not silently cut, and an unreviewed file is not auto."""

import pytest

from jev_judge_mcp.limits import GATE
from tests.support.jev import call_tool

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


async def test_an_oversized_file_is_unreviewed_and_not_auto() -> None:
    outcome = await call_tool(
        "jev_review",
        {
            "request": "fix the parser",
            "diff": [{"path": "src/parser.py", "patch": "x" * (GATE.doc_units + 1)}],
        },
        {},
    )
    assert not outcome.is_error, outcome.text
    assert outcome.payload["partial"] is True
    assert outcome.payload["unreviewed_files"] == ["src/parser.py"]
    assert outcome.payload["action"] != "auto"
    assert outcome.requests == []


async def test_two_files_under_the_cap_are_reviewed_even_if_the_join_is_not() -> None:
    """A joined string over the cap must not become one silent cut. Each file is asked."""
    half = "y" * (GATE.doc_units // 2 + 1)
    outcome = await call_tool(
        "jev_review",
        {
            "request": "fix the parser",
            "diff": [
                {"path": "src/a.py", "patch": half},
                {"path": "src/b.py", "patch": half},
            ],
        },
        {
            "correctness": {"score": 2, "confidence": 0.95},
            "spec_match": {"score": 2, "confidence": 0.95},
            "test_gap": {"score": 0, "confidence": 0.95},
            "blast_radius": {"score": 0, "confidence": 0.95},
            "safe_to_apply": {"noul": 0.95},
        },
    )
    assert not outcome.is_error, outcome.text
    assert outcome.payload["unreviewed_files"] == []
    assert outcome.payload["partial"] is False
    assert len(outcome.requests) == 2
    assert outcome.payload["action"] == "auto"
    assert outcome.payload["score_file"] == "src/a.py"
    assert outcome.payload["reviewed_files"] == ["src/a.py", "src/b.py"]


async def test_gate_reviews_a_file_list_per_file() -> None:
    outcome = await call_tool(
        "jev_gate",
        {
            "request": "fix the parser",
            "diff": [
                {"path": "src/a.py", "patch": "+ a"},
                {"path": "src/b.py", "patch": "+ b"},
            ],
            "claims": ["both files changed"],
            "evidence": [{"id": "log", "text": "2 passed"}],
        },
        {
            "correctness": {"score": 2, "confidence": 0.95},
            "spec_match": {"score": 2, "confidence": 0.95},
            "test_gap": {"score": 0, "confidence": 0.95},
            "blast_radius": {"score": 0, "confidence": 0.95},
            "safe_to_apply": {"noul": 0.95},
            "claim_0": {
                "choice": "verified",
                "confidence": 0.95,
                "probabilities": {"verified": 0.95, "contradicted": 0.03, "unsupported": 0.02},
            },
        },
    )
    assert not outcome.is_error, outcome.text
    assert outcome.payload["partial"] is False
    assert outcome.payload["unreviewed_files"] == []
    assert len(outcome.requests) == 2


async def test_gate_summary_partitions_and_a_row_stands_only_when_auto() -> None:
    """by_verdict and by_action each sum to the claim count. stands is not a grep of verified."""
    outcome = await call_tool(
        "jev_gate",
        {
            "request": "reject empty input",
            "diff": "+ return 404",
            "claims": ["empty input is rejected"],
            "evidence": [{"id": "log", "text": "test_empty: PASS"}],
        },
        {
            "correctness": {"score": 2, "confidence": 0.95},
            "spec_match": {"score": 2, "confidence": 0.95},
            "test_gap": {"score": 0, "confidence": 0.95},
            "blast_radius": {"score": 0, "confidence": 0.95},
            "safe_to_apply": {"noul": 0.95},
            "claim_0": {
                "choice": "verified",
                "confidence": 0.4,
                "probabilities": {"verified": 0.4, "contradicted": 0.3, "unsupported": 0.3},
            },
            "source_0": {"choice": "log", "probabilities": {"log": 0.5, "diff": 0.4, "tests": 0.05, "none": 0.05}},
        },
    )
    assert not outcome.is_error, outcome.text
    row = outcome.payload["verification"]["results"][0]
    summary = outcome.payload["verification"]["summary"]
    assert row["verdict"] == "verified"
    assert row["action"] == "escalate"
    assert row["stands"] is False
    assert summary["by_verdict"] == {"verified": 1}
    assert summary["by_action"] == {"escalate": 1}
    assert sum(summary["by_verdict"].values()) == 1
    assert sum(summary["by_action"].values()) == 1
