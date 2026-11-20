"""Canonical IDs on the provider wire and in client results."""

from typing import Any, cast

import pytest

from tests.support.jev import call_tool

pytestmark = pytest.mark.anyio

CLAIM_VERIFIED = {"choice": "verified", "probabilities": {"verified": 0.97, "contradicted": 0.02, "unsupported": 0.01}}
GOOD_REVIEW: dict[str, Any] = {
    "correctness": {"score": 2, "confidence": 0.95},
    "spec_match": {"score": 2, "confidence": 0.95},
    "test_gap": {"score": 0, "confidence": 0.95},
    "blast_radius": {"score": 0, "confidence": 0.95},
    "safe_to_apply": {"noul": 0.95},
}


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


async def test_find_refuses_duplicated_path_ids() -> None:
    outcome = await call_tool(
        "jev_find",
        {
            "query": "Where does the provider live?",
            "top_k": 1,
            "candidates": [
                {"id": "providers/base.py", "text": "class BaseProvider"},
                {"id": "providers/base.py", "text": "duplicate"},
                {"id": "notes:ok", "text": "elsewhere"},
            ],
        },
        {},
    )
    assert outcome.is_error and outcome.code == "invalid_arguments"
    assert outcome.text == "IDs 'providers/base.py' and 'providers/base.py' both sanitize to 'providers_base.py'."
    assert outcome.requests == []


async def test_verify_uses_canonical_evidence_ids_and_positional_claim_ids() -> None:
    outcome = await call_tool(
        "jev_verify",
        {
            "claims": ["The setup command reads the key."],
            "evidence": [
                {"id": "docs/install.md", "text": "jev-judge-mcp setup reads the key."},
                {"id": "src/setup.py", "text": "key = read_key()"},
            ],
        },
        {
            "relation_claim0": {
                "choice": "supports",
                "probabilities": {"supports": 0.97, "contradicts": 0.02, "says_nothing": 0.01},
            }
        },
    )
    assert not outcome.is_error, outcome.text
    payload = outcome.payload
    assert "renamed_ids" not in payload
    state = cast(dict[str, Any], outcome.requests[0][0])
    assert [item["id"] for item in state["evidence"]] == ["docs_install.md", "src_setup.py"]
    assert [row["id"] for row in payload["results"]] == ["claim0"]


async def test_gate_uses_canonical_caller_and_implicit_evidence_ids() -> None:
    outcome = await call_tool(
        "jev_gate",
        {
            "request": "Return 404 for unknown users.",
            "diff": "+ return res.status(404)",
            "tests": "1 passed",
            "claims": ["The unknown-user test passes."],
            "evidence": [
                {"id": "src/a.py", "text": "PASS returns 404 for an unknown id"},
                {"id": "caller", "text": "caller evidence"},
            ],
        },
        {**GOOD_REVIEW, "claim_0": CLAIM_VERIFIED},
    )
    assert not outcome.is_error, outcome.text
    payload = outcome.payload
    assert "renamed_ids" not in payload
    state, _ = outcome.requests[0]
    request_state = cast(dict[str, object], state)
    asked = cast(list[dict[str, object]], request_state["evidence"])
    assert [item["id"] for item in asked] == ["src_a.py", "caller", "diff", "tests"]


@pytest.mark.parametrize(
    ("tool", "arguments", "answers"),
    [
        pytest.param(
            "jev_find",
            {"query": "Which port?", "candidates": [{"id": "a", "text": "port 8080"}, {"id": "b", "text": "colors"}]},
            {"best": {"choice": "a", "probabilities": {"a": 0.97, "b": 0.03}}, "exists": {"noul": 0.95}},
            id="find",
        ),
        pytest.param(
            "jev_verify",
            {"claims": ["The service listens on 8080."], "evidence": "server.listen(8080)"},
            {
                "relation_claim0": {
                    "choice": "supports",
                    "probabilities": {"supports": 0.97, "contradicts": 0.02, "says_nothing": 0.01},
                }
            },
            id="verify",
        ),
        pytest.param(
            "jev_gate",
            {
                "request": "Return 404 for unknown users.",
                "diff": "+ return res.status(404)",
                "claims": ["The unknown-user test passes."],
                "evidence": "PASS returns 404 for an unknown id",
            },
            {**GOOD_REVIEW, "claim_0": CLAIM_VERIFIED},
            id="gate",
        ),
    ],
)
async def test_unchanged_ids_omit_the_field(tool: str, arguments: dict[str, Any], answers: dict[str, Any]) -> None:
    outcome = await call_tool(tool, arguments, answers)
    assert not outcome.is_error, outcome.text
    assert "renamed_ids" not in outcome.payload


async def test_find_returns_canonical_id_without_a_rename_map() -> None:
    outcome = await call_tool(
        "jev_find",
        {"query": "Where is the class?", "candidates": [{"id": "src/a.py", "text": "class A"}]},
        {"best": {"choice": "src_a.py", "probabilities": {"src_a.py": 1.0}}, "exists": {"noul": 0.95}},
    )
    assert not outcome.is_error, outcome.text
    assert [row["id"] for row in outcome.payload["top"]] == ["src_a.py"]
    assert "renamed_ids" not in outcome.payload
