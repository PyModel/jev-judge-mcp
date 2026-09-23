"""limits.py against parity-manifest.json caps (ADR-0014): a re-freeze points at one module.

Runtime never reads `docs/reference/parity-manifest.json`; this module is its only reader. Each
entry maps a (tool, field) to the regex that pulls its numbers out of the manifest's prose and
the limits.py value — or (min, max, …) tuple of values — that must equal them. int and null
manifest fields map with no regex. Two bounds the manifest's prose leaves unstated are owned
without an entry: jev_classify's classes_min (2) and every presence-only minLength 1. The manifest's
`policy.extract_reasons` pins `EXTRACT_REASON_CODES` here too (ADR-0002).
"""

import json
import re
from pathlib import Path
from typing import Final, cast

from jev_judge_mcp.extract.candidates import REGEX_TIMEOUT_S
from jev_judge_mcp.limits import (
    CANDIDATES,
    CLASSIFY,
    COMPARE,
    DECIDE,
    EXTRACT,
    FIND,
    GATE,
    RERANK,
    REVIEW,
    SANITIZE_ID_UNITS,
    SCREEN,
    VERIFY,
)
from jev_judge_mcp.policy import EXTRACT_REASON_CODES

MANIFEST: Final = Path(__file__).resolve().parents[2] / "docs/reference/parity-manifest.json"

type _Actual = int | tuple[int, ...] | None

_CASES: list[tuple[str, str, re.Pattern[str] | None, _Actual]] = [
    # jev_verify: lengths are deliberately uncapped (manifest nulls).
    ("jev_verify", "claims_min", None, VERIFY.claims_min),
    ("jev_verify", "claims_max", None, VERIFY.claims_max),
    ("jev_verify", "claim_chars_max", None, VERIFY.claim_units),
    ("jev_verify", "evidence_array_min", None, VERIFY.evidence_min),
    ("jev_verify", "evidence_array_max", None, VERIFY.evidence_max),
    # jev_screen: same — only presence is capped.
    ("jev_screen", "text_min", None, SCREEN.text_min),
    ("jev_screen", "text_max", None, SCREEN.text_max),
    ("jev_screen", "purpose_max", None, SCREEN.purpose_max),
    # jev_find and jev_rerank share one candidatesSchema; the min is stated only under jev_find.
    ("jev_find", "candidates", re.compile(r"(\d+)\.\.(\d+) reject"), (CANDIDATES.min_items, CANDIDATES.max_items)),
    ("jev_find", "candidate_text", re.compile(r"(\d+) truncate"), CANDIDATES.text_units),
    (
        "jev_find",
        "top_k",
        re.compile(r"int (\d+)\.\.(\d+), default (\d+)"),
        (FIND.top_k_min, FIND.top_k_max, FIND.top_k_default),
    ),
    ("jev_classify", "items", re.compile(r"(\d+)\.\.(\d+) reject"), (CLASSIFY.items_min, CLASSIFY.items_max)),
    ("jev_classify", "item_text", re.compile(r"(\d+) truncate"), CLASSIFY.item_units),
    ("jev_classify", "classes", re.compile(r"\.\.(\d+) reject"), CLASSIFY.classes_max),
    ("jev_classify", "class_description", re.compile(r"(\d+) truncate"), CLASSIFY.class_description_units),
    ("jev_classify", "items_x_classes", re.compile(r">(\d+) → error"), CLASSIFY.item_class_pairs),
    ("jev_decide", "decision", re.compile(r"(\d+)\.\.(\d+) reject"), (DECIDE.decision_min, DECIDE.decision_max)),
    ("jev_decide", "evidence", re.compile(r"(\d+)\.\.(\d+) reject"), (DECIDE.evidence_min, DECIDE.evidence_max)),
    ("jev_decide", "priorities", re.compile(r"(\d+)\.\.(\d+) reject"), (DECIDE.priorities_min, DECIDE.priorities_max)),
    ("jev_decide", "candidates", re.compile(r"(\d+)\.\.(\d+) reject"), (DECIDE.candidates_min, DECIDE.candidates_max)),
    ("jev_decide", "candidate_id", re.compile(r".* max (\d+)"), DECIDE.candidate_id_max),
    (
        "jev_decide",
        "candidate_description",
        re.compile(r"(\d+)\.\.(\d+)"),
        (DECIDE.candidate_description_min, DECIDE.candidate_description_max),
    ),
    # A 0 minimum is no bound: the schema states no minItems.
    (
        "jev_decide",
        "requirements",
        re.compile(r"(\d+)\.\.(\d+) reject"),
        (DECIDE.requirements_min, DECIDE.requirements_max),
    ),
    ("jev_decide", "requirement_text", re.compile(r"(\d+)\.\.(\d+)"), (DECIDE.requirement_min, DECIDE.requirement_max)),
    ("jev_rerank", "candidates", re.compile(r"\.\.(\d+) reject"), CANDIDATES.max_items),
    ("jev_rerank", "aggregate_candidate_chars", re.compile(r">(\d+) → error"), RERANK.aggregate_candidate_units),
    ("jev_rerank", "query", re.compile(r"(\d+)\.\.(\d+) reject"), (RERANK.query_min, RERANK.query_max)),
    ("jev_rerank", "top_k", re.compile(r"int (\d+)\.\.(\d+), default all"), (RERANK.top_k_min, RERANK.top_k_max)),
    ("jev_compare", "passage_a", re.compile(r"(\d+)\.\.(\d+) reject"), (COMPARE.passage_min, COMPARE.passage_max)),
    ("jev_compare", "passage_b", re.compile(r"(\d+)\.\.(\d+) reject"), (COMPARE.passage_min, COMPARE.passage_max)),
    ("jev_compare", "aspects", re.compile(r"(\d+)\.\.(\d+) reject"), (COMPARE.aspects_min, COMPARE.aspects_max)),
    ("jev_compare", "aspect_text", re.compile(r"(\d+)\.\.(\d+)"), (COMPARE.aspect_min, COMPARE.aspect_max)),
    (
        "jev_extract",
        "document",
        re.compile(r"(\d+)\.\.(\d+) reject \(then truncate at \d+, a no-op\)"),
        (EXTRACT.document_min, EXTRACT.document_max),
    ),
    ("jev_extract", "fields", re.compile(r"(\d+)\.\.(\d+) reject"), (EXTRACT.fields_min, EXTRACT.fields_max)),
    ("jev_extract", "field_id", re.compile(r".* max (\d+)"), EXTRACT.field_id_max),
    ("jev_extract", "pattern", re.compile(r"(\d+)\.\.(\d+)"), (EXTRACT.pattern_min, EXTRACT.pattern_max)),
    ("jev_extract", "flags", re.compile(r"max (\d+)"), EXTRACT.flags_max),
    ("jev_extract", "description", re.compile(r"(\d+)\.\.(\d+)"), (EXTRACT.description_min, EXTRACT.description_max)),
    ("jev_extract", "candidates_per_field", None, EXTRACT.candidates_per_field),
    ("jev_extract", "candidate_chars", re.compile(r">(\d+) → skipped.*"), EXTRACT.candidate_units),
    ("jev_extract", "aggregate_preview_chars", re.compile(r">(\d+) → error"), EXTRACT.aggregate_candidate_units),
    ("jev_extract", "regex_timeout_ms", None, EXTRACT.regex_timeout_ms),
    ("jev_review", "request_diff_tests", re.compile(r">(\d+) → truncate each, truncated=true"), REVIEW.doc_units),
    ("jev_gate", "claims", re.compile(r"(\d+)\.\.(\d+) reject"), (GATE.claims_min, GATE.claims_max)),
    ("jev_gate", "claim_chars", re.compile(r"(\d+) truncate"), GATE.claim_units),
    ("jev_gate", "evidence_items", re.compile(r">(\d+) → isError result"), GATE.evidence_items),
    ("jev_gate", "aggregate_evidence_chars", re.compile(r">(\d+) → isError result\..*"), GATE.aggregate_evidence_units),
    (
        "jev_gate",
        "doc_chars",
        re.compile(r"(\d+) truncate each \(request, diff, tests, evidence item\)"),
        GATE.doc_units,
    ),
]


def _manifest() -> dict[str, dict[str, object]]:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def _caps() -> dict[str, dict[str, object]]:
    caps = _manifest()["caps"]
    assert isinstance(caps, dict)
    return cast(dict[str, dict[str, object]], caps)


def test_limits_match_parity_manifest() -> None:
    caps = _caps()
    for tool, field, pattern, actual in _CASES:
        spec = caps[tool][field]
        if isinstance(spec, str):
            assert pattern is not None, f"{tool}.{field}: prose needs an extraction regex"
            match = pattern.fullmatch(spec)
            assert match is not None, f"{tool}.{field}: {spec!r} left the extraction map behind"
            numbers = tuple(int(group) for group in match.groups())
            assert actual == (numbers[0] if len(numbers) == 1 else numbers), f"{tool}.{field}: {spec!r}"
        else:
            assert spec is None or isinstance(spec, int), f"{tool}.{field}: unexpected spec {spec!r}"
            assert actual == spec, f"{tool}.{field}: {spec!r}"


def test_every_manifest_cap_field_is_owned() -> None:
    """A re-freeze that adds a cap field cannot land silently with no limits.py owner."""
    mapped = {(tool, field) for tool, field, _, _ in _CASES}
    for tool, fields in _caps().items():
        if tool == "$comment":
            continue
        assert isinstance(fields, dict), tool
        for field in fields:
            assert (tool, field) in mapped, f"caps.{tool}.{field} has no limits.py owner"


def test_caps_own_all_three_behaviors() -> None:
    """The manifest's three behaviors — reject, truncate, strict-greater-than error — are each owned."""
    caps = _caps()
    texts: list[str] = []
    for tool, field, _, _ in _CASES:
        spec = caps[tool][field]
        if isinstance(spec, str):
            texts.append(spec)
    assert any("reject" in text for text in texts)
    assert any("truncate" in text for text in texts)
    assert any("→ error" in text for text in texts)


def test_extract_regex_timeout_equals_limits() -> None:
    """The candidate caps reach the matcher from `EXTRACT` (`test_regex_executor.py`); the regex
    deadline is `candidates.py`'s own copy, and drift fails here."""
    assert REGEX_TIMEOUT_S == EXTRACT.regex_timeout_ms / 1000


def test_extract_reason_codes_match_parity_manifest() -> None:
    """jev_extract's Reason Codes are the manifest's, in order: a re-freeze that adds, drops, or
    reorders one fails here, not in a fixture replay."""
    assert list(EXTRACT_REASON_CODES) == _manifest()["policy"]["extract_reasons"]


def test_extension_tool_caps_are_owned_by_the_adr() -> None:
    """jev_score has no parity-manifest block: ADR-0048 owns its caps, and the published schema is
    the tie (the FindCaps precedent). A hand-inlined number in either place fails here."""
    from jev_judge_mcp.limits import SCORE
    from jev_judge_mcp.tools import score

    schema = score.DEFINITION.input_schema
    levels = schema["properties"]["levels"]
    assert levels["minItems"] == SCORE.levels_min == 2
    assert levels["maxItems"] == SCORE.levels_max == 10  # docs/jev_docs/primitives.md: 1-10 levels
    assert levels["items"]["maxLength"] == SCORE.level_units_max == 200
    assert schema["properties"]["subject"]["maxLength"] == SCORE.subject_max == 1500
    assert schema["properties"]["context"]["maxLength"] == SCORE.context_max == 12_000


def test_schema_only_caps_stay_owned_by_limits() -> None:
    """Caps the frozen manifest does not record still live in `limits.py`, tied to the published
    schema instead: jev_find's query bound (the manifest's `jev_find` block has no `query` field)
    and the sanitize-id slice. A hand-inlined number in either place fails here."""
    from jev_judge_mcp import ids
    from jev_judge_mcp.tools import find

    schema = find.DEFINITION.input_schema
    assert schema["properties"]["query"]["minLength"] == FIND.query_min
    assert ids.MAX_ID_LENGTH == SANITIZE_ID_UNITS
