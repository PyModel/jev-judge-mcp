"""Additive response fields. Old keys stay. Nothing here renames a verdict or drops a key.

The parity expectation applies this same function to a recorded payload so the only
difference from the reference text is these fields, in this order.
"""

from collections.abc import Mapping, Sequence
from typing import Literal

from jev_judge_mcp.domain.answers import RUBRIC_SCORE_MAX
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

NO_HUNKS_CHECK = (
    "diff carries no patch hunks: pass the real diff (git diff output, or a [{path, patch}] list) "
    "so claims can rest on it."
)
"""The deterministic hint a gate that did not stand appends when its `diff` was plain text (ADR-0062
amendment, 2026-10-01): a summary written in place of the patch is unsupported by construction,
and the caller should learn that from the payload, not from a bare escalate."""

type DiffShape = Literal["patch", "file_list", "text"]

_HUNK_HEADERS = ("diff --git ", "@@ ", "+++ ", "--- ", "Index: ")

SCORE_SCALE = [0, RUBRIC_SCORE_MAX]
_LEVELS = tuple(range(RUBRIC_SCORE_MAX + 1))


def diff_shape(diff: object) -> DiffShape:
    """What the caller sent as `diff`, computed in code, never judged.

    A file list is a patch by construction. A string is a patch when any line carries a unified-diff
    header or begins with `+` (an added line; `-` alone is also a bullet, so it does not count).
    Anything else — a change summary, an excerpt — is `text`: nothing a claim can rest on as a patch.
    """
    if isinstance(diff, list):
        return "file_list"
    lines = diff.splitlines() if isinstance(diff, str) else []
    return "patch" if any(line.startswith(_HUNK_HEADERS) or line.startswith("+") for line in lines) else "text"


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
    """A fixed code for an unsupported or contradicted claim. Never model prose.

    `None` when the evidence already carries a diff, tests, and a before/after pair: the claim
    failed on complete evidence, and no fixed code names something to add.
    """
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
    return None


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


def next_checks_for(codes: Sequence[object], *, diff_shape: DiffShape | None = None) -> list[str]:
    """The fixed next check per reason code, in code order, plus `NO_HUNKS_CHECK` when the call did
    not stand (`accepted` absent) and its `diff` was plain text."""
    checks = [NEXT_CHECKS[str(code)] for code in codes if str(code) in NEXT_CHECKS]
    if diff_shape == "text" and not any(str(code) == "accepted" for code in codes):
        checks.append(NO_HUNKS_CHECK)
    return checks


def nearest_level(score: object) -> int | None:
    if isinstance(score, bool) or not isinstance(score, (int, float)):
        return None
    return min(_LEVELS, key=lambda level: (abs(level - float(score)), level))
