"""Q4 on jev_verify: malformed confidence invalidates; absent confidence reviews (ADR-0012, ADR-0043).

The recorded fixture payloads for these cases stay the parity suite's job. These tests lock the
distinction the tool now reads from `ChoiceAnswer.confidence_kind`, and that the kind never
appears in the tool JSON.
"""

import math
from collections.abc import Mapping

import pytest

from jev_judge_mcp.responses import missing_evidence_code
from tests.support.jev import call_tool

pytestmark = pytest.mark.anyio

RELATION = {"choice": "supports", "probabilities": {"supports": 0.95, "contradicts": 0.025, "says_nothing": 0.025}}
ONE_CLAIM = {"claims": ["The sky is blue."], "evidence": "The sky is blue on a clear day."}


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _relation(confidence: object, *, present: bool) -> dict[str, object]:
    answer: dict[str, object] = dict(RELATION)
    if present:
        answer["confidence"] = confidence
    return answer


async def test_null_confidence_reviews() -> None:
    outcome = await call_tool("jev_verify", ONE_CLAIM, {"relation_claim0": _relation(None, present=True)})
    assert not outcome.is_error, outcome.text
    row = outcome.payload["results"][0]
    assert row["verdict"] == "verified"
    assert row["confidence"] is None
    assert row["action"] == "review"
    assert "status" not in row
    assert "confidence_kind" not in outcome.text
    assert outcome.payload["summary"] == {
        "verified": 1,
        "contradicted": 0,
        "unsupported": 0,
        "needs_review": 1,
        "by_verdict": {"verified": 1},
        "by_action": {"review": 1},
    }


async def test_missing_confidence_reviews() -> None:
    outcome = await call_tool("jev_verify", ONE_CLAIM, {"relation_claim0": _relation(None, present=False)})
    assert not outcome.is_error, outcome.text
    row = outcome.payload["results"][0]
    assert row["verdict"] == "verified"
    assert row["confidence"] is None
    assert row["action"] == "review"
    assert "status" not in row


@pytest.mark.parametrize("confidence", ["0.99", 2, -1, True, False, {}, [], math.nan, math.inf, 1.7])
async def test_malformed_confidence_invalidates(confidence: object) -> None:
    outcome = await call_tool(
        "jev_verify",
        {"claims": ["Valid claim", "Invalid claim"], "evidence": "text"},
        {
            "relation_claim0": {**RELATION, "confidence": 0.99},
            "relation_claim1": {**RELATION, "confidence": confidence},
        },
    )
    assert not outcome.is_error, outcome.text
    valid, invalid = outcome.payload["results"]
    assert valid["verdict"] == "verified"
    assert valid["confidence"] == 0.99
    assert valid["action"] == "auto"
    assert "status" not in valid
    assert invalid == {
        "id": "claim1",
        "claim": "Invalid claim",
        "verdict": "unknown",
        "probabilities": None,
        "confidence": None,
        "status": "invalid_response",
        "action": "review",
        "supporting_evidence": None,
        "stands": False,
        "evidence_ids": [],
        "excerpt": None,
        "missing_evidence": None,
    }
    assert "confidence_kind" not in outcome.text


@pytest.mark.parametrize(("confidence", "action"), [(0, "review"), (0.99, "auto"), (1, "auto")])
async def test_numeric_confidence_keeps_the_relation(confidence: float, action: str) -> None:
    outcome = await call_tool("jev_verify", ONE_CLAIM, {"relation_claim0": {**RELATION, "confidence": confidence}})
    assert not outcome.is_error, outcome.text
    row = outcome.payload["results"][0]
    assert row["verdict"] == "verified"
    assert row["confidence"] == confidence
    assert row["action"] == action
    assert "status" not in row


async def test_malformed_source_confidence_does_not_invalidate_the_relation() -> None:
    """Q4 reads the relation's kind. A source answer is auxiliary and its confidence is not Q4."""
    evidence: list[Mapping[str, str]] = [{"id": "a", "text": "one"}, {"id": "b", "text": "two"}]
    outcome = await call_tool(
        "jev_verify",
        {"claims": ["The sky is blue."], "evidence": evidence},
        {
            "relation_claim0": dict(RELATION),
            "source_claim0": {
                "choice": "a",
                "probabilities": {"a": 0.9, "b": 0.05, "none": 0.05},
                "confidence": "bad",
            },
        },
    )
    assert not outcome.is_error, outcome.text
    row = outcome.payload["results"][0]
    assert row["verdict"] == "verified"
    assert row["confidence"] is None
    assert row["action"] == "review"
    assert row["supporting_evidence"] == "a"
    assert "status" not in row


def test_a_claim_that_fails_on_complete_evidence_has_no_missing_evidence_code() -> None:
    """The fixed codes name what to add; with a diff, tests, and a before/after pair present there
    is nothing to add, so the row says `None` rather than asking for a diff that is already there."""
    complete = [
        {"id": "diff", "text": "+x", "kind": "diff", "role": "after"},
        {"id": "tests", "text": "1 passed", "kind": "tool_output", "role": "current"},
        {"id": "old", "text": "y", "role": "before"},
    ]
    assert missing_evidence_code(verdict="unsupported", evidence=complete) is None
    assert missing_evidence_code(verdict="contradicted", evidence=complete) is None
    without_tests = [item for item in complete if item["id"] != "tests"]
    assert missing_evidence_code(verdict="unsupported", evidence=without_tests) == "needs_tests"
    assert missing_evidence_code(verdict="verified", evidence=complete) is None


async def test_a_caller_note_literally_named_none_still_blocks_auto() -> None:
    """The `none` source hatch shares its key with a caller item called `none`; when that item is a
    caller note, the ADR-0067 rule still holds — the collision never turns the note into auto."""
    evidence = [
        {"id": "none", "text": "I ran the tests myself.", "kind": "caller_note"},
        {"id": "b", "text": "unrelated"},
    ]
    answers = {
        "relation_claim0": _relation(0.97, present=True),
        "source_claim0": {"choice": "none", "probabilities": {"none": 0.9, "b": 0.1}},
    }
    outcome = await call_tool("jev_verify", {"claims": ["Tests pass."], "evidence": evidence}, answers)
    assert not outcome.is_error, outcome.text
    row = outcome.payload["results"][0]
    assert row["verdict"] == "verified" and row["confidence"] == 0.97
    assert row["action"] == "review"
    assert row["supporting_evidence"] is None  # the hatch still reads as no source on the wire
