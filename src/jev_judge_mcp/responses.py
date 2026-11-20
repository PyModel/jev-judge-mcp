"""Additive response fields. Old keys stay. Nothing here renames a verdict or drops a key.

The parity expectation applies this same function to a recorded payload so the only
difference from the reference text is these fields, in this order.
"""

import re
from collections.abc import Mapping, Sequence
from typing import Literal, cast

from jev_judge_mcp.domain.answers import RUBRIC_SCORE_MAX
from jev_judge_mcp.text import head

EXCERPT_UNITS = 200
"""First line of a supporting evidence item, capped. Not a model-written why."""

MISSING_EVIDENCE = ("needs_diff", "needs_tests", "needs_before_after", "single_item_no_source")

NO_HUNKS_CHECK = (
    "diff carries no patch hunks: pass the real diff (git diff output, or a [{path, patch}] list) "
    "so claims can rest on it."
)
"""The deterministic hint a gate that did not stand appends when its `diff` was plain text (ADR-0062
amendment, 2026-10-01): a summary written in place of the patch is unsupported by construction,
and the caller should learn that from the payload, not from a bare escalate."""

EXCERPT_CHECK = (
    "diff looks trimmed by hand: a hunk's lines do not match its @@ header. The review scores only "
    "what it sees, so an excerpt scores low. Pass the real git diff output, or a [{path, patch}] list "
    "split by concern."
)
"""The deterministic hint a gate that did not stand appends when a patch's hunk headers disagree with
the lines under them (ADR-0062 amendment, 2026-10-07): an excerpt or paraphrase shaped like a patch."""

type DiffShape = Literal["patch", "file_list", "text", "excerpt"]

_FILE_HEADERS = re.compile(r"^--- [^\n]*\n\+\+\+ ", re.MULTILINE)
_HUNK_COUNTS = re.compile(r"@@ -\d+(?:,(\d+))? \+\d+(?:,(\d+))? @@", re.ASCII)

SCORE_SCALE = [0, RUBRIC_SCORE_MAX]


def diff_shape(diff: object) -> DiffShape:
    """What the caller sent as `diff`, computed in code, never judged.

    A file list is a patch by construction. A string needs a hunk header or paired file headers.
    Anything else — a change summary — is `text`: nothing a claim can rest on as a patch. A patch, or
    any file-list patch, whose hunk headers disagree with the lines under them is `excerpt`.
    """
    if isinstance(diff, list):
        patches = [
            cast("Mapping[str, object]", item).get("patch")
            for item in cast("list[object]", diff)
            if isinstance(item, Mapping)
        ]
        return "excerpt" if any(isinstance(patch, str) and _trimmed(patch) for patch in patches) else "file_list"
    if not isinstance(diff, str):
        return "text"
    if not _FILE_HEADERS.search(diff) and not any(_HUNK_COUNTS.match(line) for line in diff.split("\n")):
        return "text"
    return "excerpt" if _trimmed(diff) else "patch"


def hunk_counts(line: str, patch_size: int) -> tuple[int, int] | None:
    counts = _HUNK_COUNTS.match(line)
    if counts is None:
        return None
    old, new = counts.groups()
    if any(len(count) > len(str(patch_size)) for count in (old, new) if count):
        return None
    return (1 if old is None else int(old), 1 if new is None else int(new))


def _trimmed(patch: str) -> bool:
    """Whether a hunk header disagrees with the lines under it: a header git never writes, a hunk
    that ends early or overruns, or a line inside a hunk that is not context, removal, or addition.

    Lines split on `\\n` alone, as git writes them: `str.splitlines` also breaks on form feeds and
    Unicode separators inside a source line, which would miscount a real hunk. Text with no `@@ `
    header has no counts to check and is never called trimmed; an added line
    after a complete hunk and outside any new one is an overrun; a context line
    whose leading space was stripped still counts as context.
    """
    old = new = 0
    seen = False
    for raw in patch.split("\n"):
        line = raw.removesuffix("\r")  # a diff pasted with CRLF endings is still the same patch
        if old == 0 and new == 0:
            if line.startswith("@@ "):
                counts = hunk_counts(line, len(patch))
                if counts is None:
                    return True
                old, new = counts
                seen = True
            elif seen and line.startswith("+") and not line.startswith("+++ "):
                return True
            continue
        if line.startswith("\\"):
            continue
        if line.startswith(" ") or not line:
            old, new = old - 1, new - 1
        elif line.startswith("-"):
            old -= 1
        elif line.startswith("+"):
            new -= 1
        else:
            return True
        if old < 0 or new < 0:
            return True
    return old != 0 or new != 0


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
        "stands": row.get("action") == "auto" and row.get("verdict") == "verified",
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


def nearest_level(score: object, count: int = RUBRIC_SCORE_MAX + 1) -> int | None:
    if isinstance(score, bool) or not isinstance(score, (int, float)):
        return None
    return min(range(count), key=lambda level: abs(level - float(score)))
