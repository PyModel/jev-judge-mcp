"""Hypothesis properties for UTF-16 text, ids, and the serializer."""

import json
import math
import re
from typing import cast

from hypothesis import given
from hypothesis import strategies as st

from jev_judge_mcp.ids import ensure_unique_ids, sanitize_id
from jev_judge_mcp.serialize import stringify
from jev_judge_mcp.text import TRUNCATION_MARKER, length, truncate
from jev_judge_mcp.validation.caps import CapLedger

# Any code point, including lone surrogates, weighted toward the astral and surrogate edge cases.
TEXT = st.text(
    alphabet=st.one_of(
        st.characters(exclude_categories=()), st.sampled_from(["\ud83d", "\ude00", "😀", "a", "_", "/"])
    ),
    max_size=40,
)
SAFE_ID = re.compile(r"[A-Za-z0-9.-]([A-Za-z0-9_.-]{0,63})?")


@given(TEXT, st.integers(min_value=0, max_value=50))
def test_truncate_is_a_bounded_prefix(text: str, cap: int) -> None:
    out = truncate(text, cap)
    if length(text) <= cap:
        assert out == text
    else:
        head = out.removesuffix(TRUNCATION_MARKER)
        assert out.endswith(TRUNCATION_MARKER)
        assert text.startswith(head)
        assert cap - 1 <= length(head) <= cap


@given(TEXT, st.integers(min_value=0, max_value=50), st.sampled_from(["context", "item"]))
def test_cap_ledger_cuts_as_truncate_does_and_records_exactly_the_cut(text: str, cap: int, scope: str) -> None:
    ledger = CapLedger()
    assert ledger.text(text, cap, "context" if scope == "context" else "item") == truncate(text, cap)
    assert (ledger.scopes == {scope}) is (length(text) > cap)
    assert ledger.context_cut is (scope == "context" and length(text) > cap)


@given(st.text(max_size=40), st.integers(min_value=0, max_value=50))
def test_truncate_never_creates_a_lone_surrogate(text: str, cap: int) -> None:
    truncate(text, cap).encode("utf-8")


@given(TEXT)
def test_length_matches_utf16(text: str) -> None:
    assert length(text) == len(text.encode("utf-16-le", "surrogatepass")) // 2


@given(TEXT)
def test_sanitize_id_is_safe(raw: str) -> None:
    out = sanitize_id(raw)
    assert out == "" or SAFE_ID.fullmatch(out)
    assert not out.startswith("_")


@given(st.lists(st.one_of(st.none(), TEXT), max_size=20))
def test_ensure_unique_ids_are_unique_and_safe(raw_ids: list[str | None]) -> None:
    items, renamed = ensure_unique_ids([{"id": raw} for raw in raw_ids], "item")
    ids = [item["id"] for item in items]
    assert len(set(ids)) == len(ids)
    assert all(isinstance(i, str) and re.fullmatch(r"[A-Za-z0-9_.-]+", i) for i in ids)
    assert all(raw and raw != new for raw, new in renamed.items())


JSON = st.recursive(
    st.one_of(
        st.none(), st.booleans(), st.floats(allow_nan=False, allow_infinity=False), st.integers(-(2**53), 2**53), TEXT
    ),
    lambda children: st.one_of(st.lists(children, max_size=4), st.dictionaries(TEXT, children, max_size=4)),
    max_leaves=20,
)


def normalized(value: object) -> object:
    """What survives a JS round trip: every number is a double, -0 prints as 0."""
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return value
    if isinstance(value, int | float):
        return float(value) + 0.0
    if isinstance(value, dict):
        return {key: normalized(item) for key, item in cast("dict[str, object]", value).items()}
    assert isinstance(value, list)
    return [normalized(item) for item in cast("list[object]", value)]


@given(JSON)
def test_stringify_round_trips_through_a_json_parser(value: object) -> None:
    text = stringify(value)
    text.encode("utf-8")
    assert normalized(json.loads(text)) == normalized(value)


@given(st.floats(allow_nan=False, allow_infinity=False))
def test_numbers_round_trip_exactly(value: float) -> None:
    parsed = float(json.loads(stringify(value)))
    assert parsed == value or (value == 0 and parsed == 0)
    assert not math.isnan(parsed)
