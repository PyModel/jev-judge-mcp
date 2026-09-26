"""Additive response fields. Old keys stay. Nothing here renames a verdict or drops a key.

The parity expectation applies this same function to a recorded payload so the only
difference from the reference text is these fields, in this order.
"""

from collections.abc import Mapping, Sequence

from jev_judge_mcp.text import head

EXCERPT_UNITS = 200
"""First line of a supporting evidence item, capped. Not a model-written why."""

MISSING_EVIDENCE = ("needs_diff", "needs_tests", "needs_before_after", "single_item_no_source")

NEXT_CHECKS: dict[str, str] = {
    "incomplete_context": "Split the diff or pass a file list. A truncated diff is not a pass.",
    "invalid_response": "The row is unjudged. Do not treat it as verified.",
    "review_escalated": "Stop. Read action, not verdict.",
    "review_required": "Confirm this before proceeding. Read action, not verdict.",
    "claims_contradicted": "The evidence contradicts a claim. Do not ship that claim.",
    "claims_unsupported": "Read missing_evidence on the claim row. Do not grep verdict.",
    "claim_confidence_low": "Do not grep verified. Read action.",
    "claim_confidence_below_auto_accept": "The claim is not confident enough to stand. Read action.",
    "accepted": "The row stands. Proceed.",
    "caller_note_only": "A caller note is not enough for auto. Add a diff or a tool log.",
}

SCORE_SCALE = [0, 2]
_LEVELS = (0, 1, 2)


def renamed_ids_field(renamed: Mapping[str, str]) -> dict[str, dict[str, str]]:
    """`renamed_ids`: sent id → returned id, present only when at least one id changed."""
    return {"renamed_ids": dict(renamed)} if renamed else {}


def caller_renames(sent: Sequence[Mapping[str, object]], asked: Sequence[Mapping[str, object]]) -> dict[str, str]:
    """Sent id → returned id across every `ensure_unique_ids` pass a tool ran over its caller items.

    `asked` may append tool-generated items (gate's implicit diff and tests evidence); only the
    leading caller items are mapped, so an implicit id suffixed past a caller id never appears.
    """
    renamed: dict[str, str] = {}
    for raw, final in zip(sent, asked[: len(sent)], strict=True):
        raw_id = raw.get("id")
        if isinstance(raw_id, str) and raw_id and raw_id != final["id"]:
            renamed[raw_id] = str(final["id"])
    return renamed


def stands(action: object) -> bool:
    return action == "auto"


def partition(rows: Sequence[Mapping[str, object]], key: str) -> dict[str, int]:
    """Counts of ``key`` that sum to ``len(rows)``. A missing value is ``"none"``."""
    counts: dict[str, int] = {}
    for row in rows:
        label = row.get(key)
        name = "none" if label is None else str(label)
        counts[name] = counts.get(name, 0) + 1
    return counts


def excerpt_of(text: object) -> str | None:
    if not isinstance(text, str) or not text:
        return None
    line = text.split("\n", 1)[0]
    return head(line, EXCERPT_UNITS)


def missing_evidence_code(
    *,
    verdict: object,
    evidence: Sequence[Mapping[str, object]],
) -> str | None:
    """A fixed code for an unsupported or contradicted claim. Never model prose."""
    if verdict not in ("unsupported", "contradicted"):
        return None
    if len(evidence) < 2:
        return "single_item_no_source"
    kinds = {str(item.get("kind") or "raw") for item in evidence}
    ids = {str(item.get("id")) for item in evidence}
    roles = {str(item.get("role") or "current") for item in evidence}
    if "diff" not in ids and "diff" not in kinds:
        return "needs_diff"
    if "tests" not in ids and "tool_output" not in kinds:
        return "needs_tests"
    if "before" not in roles or "after" not in roles:
        return "needs_before_after"
    return "needs_diff"


def claim_extras(
    row: Mapping[str, object],
    evidence: Sequence[Mapping[str, object]],
    *,
    claim_id: str | None = None,
    supporting: object | None = None,
) -> dict[str, object]:
    """Fields appended to one claim row. Existing keys are not copied here."""
    support = supporting if supporting is not None else row.get("supporting_evidence")
    item = next((entry for entry in evidence if str(entry.get("id")) == support), None)
    extras: dict[str, object] = {
        "stands": stands(row.get("action")),
        "evidence_ids": [support] if isinstance(support, str) else [],
        "excerpt": excerpt_of(item.get("text")) if item is not None else None,
        "missing_evidence": missing_evidence_code(verdict=row.get("verdict"), evidence=evidence),
    }
    if claim_id is not None and "id" not in row:
        extras = {"id": claim_id, **extras}
    if "supporting_evidence" not in row:
        extras["supporting_evidence"] = support if isinstance(support, str) else None
    return extras


def summary_extras(rows: Sequence[Mapping[str, object]]) -> dict[str, object]:
    return {"by_verdict": partition(rows, "verdict"), "by_action": partition(rows, "action")}


def next_checks_for(codes: Sequence[object]) -> list[str]:
    return [NEXT_CHECKS[str(code)] for code in codes if str(code) in NEXT_CHECKS]


CALLER_INPUT_ERROR_PREFIXES = (
    "Duplicate ",
    "diff file list",
    "Thresholds must satisfy",
)
"""Prefixes of a ToolError that refuses the caller's own arguments before anything is asked.

Duplicate caller ids (jev_decide, jev_classify, jev_extract, jev_rerank), a diff that is not the
file-list shape (jev_review, jev_gate), and a broken auto_accept/review_at pair are argument
validation, not provider failures. A budget refusal is not here: it keeps its own
`input_too_large` code below.
"""

ESCAPE_HATCH_COLLISION = " collides with an escape hatch;"
"""Infix of jev_decide's refusal of a candidate id that shadows an escape hatch."""


def error_code(text: str) -> str:
    """A code for an ``isError`` result. The text itself is not changed."""
    if text.startswith("No Jev provider credentials") or text.startswith("jev-judge-mcp hook: fail-open"):
        return "auth"
    if (
        text.startswith("MCP error -32602")
        or ESCAPE_HATCH_COLLISION in text
        or any(text.startswith(prefix) for prefix in CALLER_INPUT_ERROR_PREFIXES)
    ):
        return "invalid_arguments"
    if "aggregate budget" in text or "exceeds the" in text:
        return "input_too_large"
    lowered = text.lower()
    if "timed out" in lowered or "timeout" in lowered:
        return "timeout"
    if "429" in text or "rate limit" in lowered:
        return "quota"
    return "provider"


def nearest_level(score: object) -> int | None:
    if isinstance(score, bool) or not isinstance(score, (int, float)):
        return None
    return min(_LEVELS, key=lambda level: (abs(level - float(score)), level))
