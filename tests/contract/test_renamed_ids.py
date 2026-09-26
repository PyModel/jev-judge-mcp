"""`renamed_ids`: a caller can map every returned id back to the id it sent (ADR-0062 amendment).

jev_find, jev_verify, and jev_gate run caller ids through `ensure_unique_ids`, and their schemas
invite file paths as ids. The descriptions said nothing about the rewrite, so a caller could not
map `providers_base.py` back to `providers/base.py`. The field is additive: sent id → returned id,
present only when at least one id changed, absent when nothing changed. The last test is the
recurrence guard: a tool that sanitizes ids and drops the renames fails it.
"""

import ast
from pathlib import Path
from typing import Any, cast

import pytest

from tests.support.jev import call_tool

pytestmark = pytest.mark.anyio

TOOLS_DIR = Path(__file__).parents[2] / "src" / "jev_judge_mcp" / "tools"

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


async def test_find_maps_sanitized_and_duplicated_ids_back_to_the_caller() -> None:
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
        {
            "best": {
                "choice": "providers_base.py_1",
                "probabilities": {"providers_base.py": 0.02, "providers_base.py_1": 0.96, "notes_ok": 0.02},
            },
            "exists": {"noul": 0.95},
        },
    )
    assert not outcome.is_error, outcome.text
    payload = outcome.payload
    assert payload["renamed_ids"] == {"providers/base.py": "providers_base.py_1", "notes:ok": "notes_ok"}
    assert [row["id"] for row in payload["top"]] == ["providers_base.py_1"]


async def test_verify_maps_evidence_ids_and_not_generated_claim_ids() -> None:
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
    # claim0 is a generated id, never sent: it must not appear as a sent key or a returned value.
    assert payload["renamed_ids"] == {"docs/install.md": "docs_install.md", "src/setup.py": "src_setup.py"}
    assert "claim0" not in payload["renamed_ids"]


async def test_gate_maps_evidence_ids_and_keeps_implicit_collisions_out() -> None:
    outcome = await call_tool(
        "jev_gate",
        {
            "request": "Return 404 for unknown users.",
            "diff": "+ return res.status(404)",
            "tests": "1 passed",
            "claims": ["The unknown-user test passes."],
            "evidence": [
                {"id": "src/a.py", "text": "PASS returns 404 for an unknown id"},
                {"id": "diff", "text": "caller evidence that happens to be named diff"},
            ],
        },
        {**GOOD_REVIEW, "claim_0": CLAIM_VERIFIED},
    )
    assert not outcome.is_error, outcome.text
    payload = outcome.payload
    # The caller's "diff" is unchanged; the implicit diff item is the one suffixed.
    assert payload["renamed_ids"] == {"src/a.py": "src_a.py"}
    state, _ = outcome.requests[0]
    request_state = cast(dict[str, object], state)
    asked = cast(list[dict[str, object]], request_state["evidence"])
    assert [item["id"] for item in asked] == ["src_a.py", "diff", "diff_1", "tests"]


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


def test_every_tool_that_sanitizes_ids_surfaces_the_renames() -> None:
    """Guard: a tool module that calls `ensure_unique_ids` must carry the rename map into its payload.

    The original bug was exactly this drop: `ensure_unique_ids(...).items` and no `renamed` anywhere.
    """
    offenders: list[str] = []
    for path in sorted(TOOLS_DIR.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        sanitizes = any(
            isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "ensure_unique_ids"
            for node in ast.walk(tree)
        )
        has_handle = any(isinstance(node, ast.AsyncFunctionDef) and node.name == "handle" for node in ast.walk(tree))
        if not (sanitizes and has_handle):
            continue
        source = path.read_text(encoding="utf-8")
        if "renamed_ids" not in source or not ("caller_renames" in source or ".renamed" in source):
            offenders.append(path.name)
    assert not offenders, f"tools that sanitize ids but drop the renames: {offenders}"
