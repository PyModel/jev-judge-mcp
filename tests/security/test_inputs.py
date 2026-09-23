# ruff: noqa: RUF001 - the corpus is made of look-alike characters on purpose
"""Hostile inputs (ROADMAP P6): astral, combining, and RTL Unicode; duplicate and 64+-char ids; oversized
everything. Every size comes from the published schemas or `limits.py`, never a literal, so a re-freeze
moves the probes with the caps. Lengths are UTF-16 units (ADR-0005): padding is astral on purpose.
"""

import json
import time
from collections.abc import Iterator
from typing import Any, cast

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from jev_judge_mcp.limits import CLASSIFY, GATE, REVIEW
from jev_judge_mcp.text import TRUNCATION_MARKER, length
from jev_judge_mcp.tools import TOOLS
from tests.security.tools import BY_TOOL, CASES, Path, path_id, with_value
from tests.support.jev import Outcome, call_tool

pytestmark = pytest.mark.anyio

SCHEMA_REJECT = "MCP error -32602"

UNICODE = [
    "😀a😀",  # astral beside BMP
    "e\u0301\u0302 a\u0308\u0323 Z\u0334\u0322\u031b\u0356\u0353\u0308\u0301",  # stacked combining marks
    "\u202eevil\u202c \u2067مرحبا بالعالم\u2069 שלום",  # RTL text and bidi controls
    "\u200b\u200d\ufeff\u2028\u2029\u0085\u00a0",  # zero-width, BOM, line/paragraph separators, NEL, NBSP
    "👩\u200d👩\u200d👧\u200d👦🏳\ufe0f\u200d🌈🇺🇳",  # ZWJ sequences and flags
]


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def pad(units: int) -> str:
    """Exactly `units` UTF-16 units, astral wherever possible, so `len()` and `length()` disagree."""
    text = "😀" * (units // 2) + "a" * (units % 2)
    assert length(text) == units
    return text


def get_path(arguments: Any, path: Path) -> Any:
    for part in path:
        arguments = arguments[part]
    return arguments


def has_path(arguments: Any, path: Path) -> bool:
    try:
        get_path(arguments, path)
    except (KeyError, IndexError, TypeError):
        return False
    return True


def bounded(schema: dict[str, Any], path: Path = ()) -> Iterator[tuple[Path, dict[str, Any]]]:
    """Every string with `maxLength` and array with `maxItems` in a published schema."""
    if schema.get("type") == "string" and "maxLength" in schema:
        yield path, schema
    if schema.get("type") == "array":
        if "maxItems" in schema:
            yield path, schema
        yield from bounded(schema["items"], (*path, 0))
    for key, child in schema.get("properties", {}).items():
        yield from bounded(child, (*path, key))
    for option in schema.get("anyOf", []):
        yield from bounded(option, path)


REJECT_ROWS = [
    pytest.param(tool.name, path, schema, id=f"{tool.name}-{path_id(path)}")
    for tool in TOOLS
    for path, schema in bounded(tool.definition.input_schema)
    if has_path(BY_TOOL[tool.name].arguments, path)
]


@pytest.mark.parametrize(("tool", "path", "schema"), REJECT_ROWS)
async def test_one_over_the_cap_is_rejected_before_any_request(tool: str, path: Path, schema: dict[str, Any]) -> None:
    case = BY_TOOL[tool]
    if "maxLength" in schema:
        over, limit = pad(schema["maxLength"] + 1), f"at most {schema['maxLength']} character"
    else:
        over, limit = [get_path(case.arguments, (*path, 0))] * (schema["maxItems"] + 1), "at most"
    outcome = await call_tool(tool, with_value(case.arguments, path, over), case.permissive)
    assert outcome.is_error
    assert outcome.text.startswith(SCHEMA_REJECT), outcome.text
    assert limit in outcome.text
    assert outcome.requests == []


@pytest.mark.parametrize(
    ("tool", "path", "schema"), [row for row in REJECT_ROWS if "maxLength" in cast(dict[str, Any], row.values[2])]
)
async def test_exactly_the_cap_passes_the_schema(tool: str, path: Path, schema: dict[str, Any]) -> None:
    case = BY_TOOL[tool]
    value = pad(schema["maxLength"]) if path[-1] != "id" else "a" * schema["maxLength"]
    outcome = await call_tool(tool, with_value(case.arguments, path, value), case.permissive)
    assert not outcome.text.startswith(SCHEMA_REJECT), outcome.text


def schema_path(path: Path) -> Path:
    """`path` with every array index at 0, as `bounded` reports it."""
    return tuple(0 if isinstance(part, int) else part for part in path)


CAPPED = {(row.values[0], row.values[1]) for row in REJECT_ROWS}
HUGE_ROWS = [
    pytest.param(case, path, id=f"{case.tool}-{path_id(path)}")
    for case in CASES
    for path in case.text_paths
    if (case.tool, schema_path(path)) not in CAPPED
]


@pytest.mark.parametrize(("case", "path"), HUGE_ROWS)
async def test_a_megabyte_in_any_open_field_is_handled(case: Any, path: Path) -> None:
    """No schema cap: truncated, budget-refused, or passed through, but answered, and fast."""
    started = time.monotonic()
    outcome = await call_tool(case.tool, with_value(case.arguments, path, pad(1_000_000)), case.permissive)
    assert time.monotonic() - started < 10
    assert not outcome.text.startswith(SCHEMA_REJECT), outcome.text
    if not outcome.is_error:
        json.loads(outcome.text)


@pytest.mark.parametrize("tool", ["jev_review", "jev_gate"])
@pytest.mark.parametrize("field", ["request", "diff", "tests"])
async def test_truncated_input_is_never_auto(tool: str, field: str) -> None:
    """Permissive answers, one field over the doc cap: the context is incomplete, so no auto."""
    case = BY_TOOL[tool]
    cap = REVIEW.doc_units if tool == "jev_review" else GATE.doc_units
    at_cap = await call_tool(tool, with_value(case.arguments, (field,), pad(cap)), case.permissive)
    assert case.acts(at_cap.payload), at_cap.text
    over = await call_tool(tool, with_value(case.arguments, (field,), pad(cap + 1)), case.permissive)
    assert not case.acts(over.payload), over.text


async def test_gate_evidence_budgets_are_errors_not_truncation() -> None:
    case = BY_TOOL["jev_gate"]
    items = [{"id": f"e{i}", "text": "x"} for i in range(GATE.evidence_items + 1)]
    outcome = await call_tool("jev_gate", {**case.arguments, "evidence": items}, case.permissive)
    assert outcome.is_error and outcome.requests == []
    per_item = GATE.aggregate_evidence_units // GATE.evidence_items + 1
    items = [{"id": f"e{i}", "text": pad(per_item)} for i in range(GATE.evidence_items)]
    outcome = await call_tool("jev_gate", {**case.arguments, "evidence": items}, case.permissive)
    assert outcome.is_error and outcome.requests == []
    assert "aggregate budget" in outcome.text


UNICODE_ROWS = [
    pytest.param(case, path, text, id=f"{case.tool}-{path_id(path)}-{index}")
    for case in CASES
    for path in case.text_paths
    for index, text in enumerate(UNICODE)
]


@pytest.mark.parametrize(("case", "path", "text"), UNICODE_ROWS)
async def test_unicode_reaches_the_provider_verbatim(case: Any, path: Path, text: str) -> None:
    value = f"{get_path(case.arguments, path)} {text}"
    outcome = await call_tool(case.tool, with_value(case.arguments, path, value), case.permissive)
    assert not outcome.is_error, outcome.text
    sent = [json.dumps(request, ensure_ascii=False) for request in outcome.requests]
    assert any(json.dumps(value, ensure_ascii=False)[1:-1] in body for body in sent)
    assert case.acts(outcome.payload), outcome.text
    json.loads(outcome.text)


async def test_truncation_counts_utf16_units_and_never_splits_a_pair() -> None:
    """jev_classify truncates item text at its cap in UTF-16 units. A cut through a surrogate pair drops
    the high half instead of sending a lone surrogate (ADR-0005, quirk Q8)."""
    case = BY_TOOL["jev_classify"]
    text = "a" + pad(CLASSIFY.item_units + 10)
    outcome = await call_tool("jev_classify", with_value(case.arguments, ("items", 0, "text"), text), case.permissive)
    sent = json.dumps(outcome.requests[0], ensure_ascii=False)
    kept = text[: 1 + (CLASSIFY.item_units - 1) // 2]
    assert length(kept) == CLASSIFY.item_units - 1
    assert json.dumps(kept + TRUNCATION_MARKER, ensure_ascii=False)[1:-1] in sent


# ADR-0004 astral fuzz, end to end through jev_extract. Expectations are V8's (`String.prototype.matchAll`,
# no `u` flag), pinned here; the full differential corpus is tests/parity/test_extract_differential.py.
ASTRAL = [
    (".", "😀a", ["\ud83d", "\ude00", "a"]),
    ("\\s", "a\u00a0b\u0085c\u2028d\ufeffe", ["\u00a0", "\u2028", "\ufeff"]),
    ("\\ud83d\\ude00", "x😀y", ["😀"]),
    ("\\ud83d", "😀\ud83d", ["\ud83d"]),
    ("\\S+", "مرحبا e\u0301 😀", ["مرحبا", "e\u0301", "😀"]),
    ("[^a]", "a😀", ["\ud83d", "\ude00"]),
    ("\\w+", "café 😀x", ["caf", "x"]),
]


def sent_candidates(outcome: Outcome) -> list[str]:
    """The candidate values jev_extract put in its question's criteria, in order."""
    if not outcome.requests:
        return []
    questions: Any = outcome.requests[0][1]
    criteria: dict[str, str] = questions["f0"]["criteria"]
    return [
        json.loads(value.removeprefix("Candidate value: ")) for key, value in criteria.items() if key != "none_of_them"
    ]


def extract_answer(candidates: int) -> dict[str, Any]:
    probabilities = {f"c{i}": 0.0 for i in range(candidates)} | {"none_of_them": 0.0}
    probabilities["c0"] = 1.0
    return {"f0": {"choice": "c0", "probabilities": probabilities, "confidence": 0.99}}


@pytest.mark.parametrize(("pattern", "document", "expected"), ASTRAL, ids=[p for p, _, _ in ASTRAL])
async def test_astral_candidates_match_v8(pattern: str, document: str, expected: list[str]) -> None:
    arguments = {"document": document, "fields": [{"id": "f", "pattern": pattern, "description": "d"}]}
    outcome = await call_tool("jev_extract", arguments, extract_answer(len(expected)))
    assert not outcome.is_error, outcome.text
    assert sent_candidates(outcome) == expected
    assert outcome.payload["results"][0]["value"] == expected[0]


ALPHABET = st.sampled_from(["a", "b", " ", "😀", "\ud83d", "\ude00", "\u00a0", "\u0085", "\u0301", "\u202e", "م", "\n"])
PATTERNS = st.sampled_from([".", "\\s", "\\s+", "\\S+", "\\w+", "[^a]", "a|😀", "\\ud83d", ".{2}", "\\s+\\S"])


@settings(max_examples=150, deadline=None)
@given(document=st.lists(ALPHABET, min_size=1, max_size=24).map("".join), pattern=PATTERNS)
async def test_astral_fuzz_invariants(document: str, pattern: str) -> None:
    """Whatever the document: every candidate is a verbatim unit slice of it, `\\s` never matches NEL
    (a whitespace-only pattern returns no NEL; in `\\s+\\S` only the final `\\S` may consume it),
    the value is a candidate or null, and the result serializes."""
    arguments = {"document": document, "fields": [{"id": "f", "pattern": pattern, "description": "d"}]}
    outcome = await call_tool("jev_extract", arguments, extract_answer(1))
    assert not outcome.is_error, outcome.text
    result = outcome.payload["results"][0]
    units = document.encode("utf-16-le", "surrogatepass")
    texts = sent_candidates(outcome)
    for text in texts:
        assert text.encode("utf-16-le", "surrogatepass") in units
        if pattern in ("\\s", "\\s+"):
            assert "\u0085" not in text
        elif pattern == "\\s+\\S":
            assert "\u0085" not in text[:-1]
    assert result["value"] is None or result["value"] in texts


async def test_duplicate_ids_are_renamed_and_answers_follow_the_rename() -> None:
    """jev_find renames collisions (`a`, `a_1`, `a_1_1`) and ranks the candidate the answer named."""
    arguments = {
        "query": "q",
        "candidates": [{"id": "a", "text": "1"}, {"id": "a", "text": "2"}, {"id": "a_1", "text": "3"}],
    }
    answers = {
        "best": {"choice": "a_1", "probabilities": {"a": 0.02, "a_1": 0.97, "a_1_1": 0.01}},
        "exists": {"noul": 0.9},
    }
    outcome = await call_tool("jev_find", arguments, answers)
    assert [(entry["id"], entry["text"]) for entry in outcome.payload["top"]] == [
        ("a_1", "2"),
        ("a", "1"),
        ("a_1_1", "3"),
    ]


async def test_ids_longer_than_64_are_cut_then_deduplicated() -> None:
    """Two 70-char ids with a shared 64-char head: sanitize cuts to 64, the collision gets `_1`."""
    long = "x" * 70
    arguments = {"query": "q", "candidates": [{"id": f"{long}1", "text": "1"}, {"id": f"{long}2", "text": "2"}]}
    outcome = await call_tool("jev_find", arguments, {})
    questions: Any = outcome.requests[0][1]
    keys: list[str] = list(questions["best"]["criteria"])
    assert keys == ["x" * 64, "x" * 64 + "_1"]


@pytest.mark.parametrize(
    ("tool", "arguments", "error"),
    [
        (
            "jev_rerank",
            {"query": "q", "candidates": [{"id": "a", "text": "1"}, {"id": "a", "text": "2"}]},
            "Duplicate candidate id: a",
        ),
        (
            "jev_classify",
            {
                "items": [{"id": "i", "text": "t"}, {"id": "i", "text": "u"}],
                "classes": [{"description": "a"}, {"description": "b"}],
            },
            "Duplicate item id: i",
        ),
        (
            "jev_decide",
            {
                "decision": "d",
                "evidence": "e",
                "priorities": "p",
                "candidates": [{"id": "a", "description": "x"}, {"id": "a", "description": "y"}],
            },
            "Duplicate candidate id: a",
        ),
        (
            "jev_extract",
            {
                "document": "ab",
                "fields": [
                    {"id": "f", "pattern": "a", "description": "d"},
                    {"id": "f", "pattern": "b", "description": "d"},
                ],
            },
            "Duplicate field id: f",
        ),
    ],
    ids=["rerank", "classify", "decide", "extract"],
)
async def test_duplicate_caller_ids_are_refused_before_any_request(tool: str, arguments: Any, error: str) -> None:
    outcome = await call_tool(tool, arguments, {})
    assert outcome.is_error
    assert outcome.text == error
    assert outcome.requests == []


async def test_long_verbatim_ids_round_trip() -> None:
    """jev_rerank keeps caller ids verbatim at any length; the answer maps to the right one."""
    first, second = "z" * 100, "z" * 99
    arguments = {"query": "q", "candidates": [{"id": first, "text": "1"}, {"id": second, "text": "2"}]}
    outcome = await call_tool("jev_rerank", arguments, {"rel_0": {"noul": 0.1}, "rel_1": {"noul": 0.9}})
    assert [(entry["id"], entry["text"]) for entry in outcome.payload["ranked"]] == [(second, "2"), (first, "1")]
