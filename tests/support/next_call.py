"""The settled `next_call` of every blocker code, and the rollup rule (ADR-0089).

Written out by hand, not derived from the server's own code, so a retagged emitter or an
unknown blocker code fails every gate and review test that reaches it.
"""

import re
from collections.abc import Mapping, Sequence
from typing import Any

from jev_judge_mcp.policy.claims import VERIFIED_WITHOUT_SOURCE
from jev_judge_mcp.responses import EXCERPT_CHECK, NO_HUNKS_CHECK

_RUBRIC = "(correctness|spec_match|test_gap|blast_radius)"
_BELOW = "(unknown|below_(review_at|auto_accept))"
_NONE = re.compile(
    "|".join(
        (
            rf"{_RUBRIC}_confidence_{_BELOW}",
            rf"({_RUBRIC}|safe_to_apply)_invalid",
            "safe_to_apply_below_(review_at|auto_accept)",
            "composite_below_floor",
            rf"claim_confidence_{_BELOW}",
            "claim_invalid|claim_contradicted|source_invalid",
            "wrong_result|state_change|error_handling|safety_refusal",
            "defect_kind_invalid|defect_line_missing|context_omitted",
        )
    )
)
_WITH_INPUT = re.compile(
    "(request|diff|tests|claims|evidence|patch)_over_budget|claim_unsupported|claim_contradiction_unconfirmed"
    "|caller_note_only|claim_verified_without_source"
)
UNANSWERED_FILE = "by hunk and retry the file review."
OVERFLOW_CHECK = "Provider context limit reached."


def expected_next_call(blocker: Mapping[str, Any]) -> str:
    code = blocker["code"]
    if code == "file_withheld":
        return "with_input" if blocker["reason"] in ("untracked", "oversize") else "none"
    if _WITH_INPUT.fullmatch(code):
        return "with_input"
    if _NONE.fullmatch(code):
        # A file the provider refused as too large: every rubric is unanswered and a split carries it.
        unanswered = code.endswith("_invalid") and blocker["next_check"].endswith(UNANSWERED_FILE)
        return "with_input" if unanswered else "none"
    raise AssertionError(f"blocker code {code!r} has no settled next_call")


def assert_next_call(payload: Mapping[str, Any]) -> None:
    """Each blocker carries its settled tag; the rollup is with_input iff some input can move the row."""
    blockers = payload.get("blockers", [])
    for blocker in blockers:
        assert blocker["next_call"] == expected_next_call(blocker), blocker
    if "rollup" not in payload:
        return
    checks = payload["next_checks"]
    repack = any(check in (NO_HUNKS_CHECK, EXCERPT_CHECK) or check.startswith(OVERFLOW_CHECK) for check in checks)
    with_input = repack or any(blocker["next_call"] == "with_input" for blocker in blockers)
    assert payload["rollup"]["next_call"] == ("with_input" if with_input else "none"), payload["rollup"]


def expected_verify_next_call(row: Mapping[str, Any], evidence: Sequence[Mapping[str, Any]], auto_accept: float) -> str:
    """A jev_verify row held only for missing support can be settled by evidence it rests on."""
    if row["action"] != "review" or row.get("status") == "invalid_response":
        return "none"
    if VERIFIED_WITHOUT_SOURCE in row.get("reason_codes", []):
        return "with_input"
    support = row["supporting_evidence"] or (str(evidence[0]["id"]) if len(evidence) == 1 else None)
    kinds = {str(item.get("id")): item.get("kind") for item in evidence}
    confident = row["confidence"] is not None and row["confidence"] >= auto_accept
    note = support is not None and kinds.get(support) == "caller_note"
    return "with_input" if confident and note else "none"


def assert_verify_next_call(payload: Mapping[str, Any], evidence: Sequence[Mapping[str, Any]]) -> None:
    rows = payload["results"]
    for row in rows:
        assert row["next_call"] == expected_verify_next_call(row, evidence, payload["auto_accept"]), row
    with_input = any(row["next_call"] == "with_input" for row in rows)
    assert payload["summary"]["next_call"] == ("with_input" if with_input else "none"), payload["summary"]
