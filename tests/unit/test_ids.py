"""Reference sanitization and the shared caller ID policy."""

import pytest

from jev_judge_mcp.ids import ensure_unique_ids, sanitize_id
from jev_judge_mcp.tools.base import ToolError


def test_sanitize_id_keeps_safe_characters_and_drops_the_rest() -> None:
    assert sanitize_id("src/lib.ts") == "src_lib.ts"
    assert sanitize_id("note: hello?!") == "note_hello"
    assert sanitize_id("???") == ""
    assert len(sanitize_id("a" * 100)) == 64


def test_sanitize_id_collapses_runs_and_strips_edges() -> None:
    assert sanitize_id("__a  b__") == "a_b"
    assert sanitize_id("a-b.c_d") == "a-b.c_d"
    assert sanitize_id("😀x😀") == "x"
    assert sanitize_id("a😀\ud83db") == "a_b"
    assert sanitize_id("line\n") == "line"
    assert sanitize_id("é") == ""


def test_sanitize_id_slices_after_stripping() -> None:
    # A 64-unit cut can end in an underscore; the reference does not strip again.
    assert sanitize_id("a" * 63 + "/b") == "a" * 63 + "_"
    assert sanitize_id("_" * 10 + "a" * 70) == "a" * 64


def test_ensure_unique_ids_refuses_repeated_caller_ids() -> None:
    raw = [{"id": "src/lib.ts", "text": "a"}, {"id": "src/lib.ts", "text": "b"}, {"text": "c"}]
    with pytest.raises(ToolError) as raised:
        ensure_unique_ids(raw, "candidate")
    assert str(raised.value) == "IDs 'src/lib.ts' and 'src/lib.ts' both sanitize to 'src_lib.ts'."


def test_sanitized_collision_names_both_original_ids_in_order() -> None:
    raw = [{"id": "a/b"}, {"id": "x"}, {"id": "a_b"}]
    with pytest.raises(ToolError) as raised:
        ensure_unique_ids(raw, "c")
    assert str(raised.value) == "IDs 'a/b' and 'a_b' both sanitize to 'a_b'."


def test_fallback_ids_leave_supplied_ids_in_place() -> None:
    raw = [{"text": "no id"}, {"id": "item0"}]
    items = ensure_unique_ids(raw, "item").items
    assert [item["id"] for item in items] == ["item2", "item0"]


def test_overflow_search_skips_taken_ids() -> None:
    items = ensure_unique_ids([{}, {"id": "c0"}, {"id": "c4"}, {}], "c").items
    assert [item["id"] for item in items] == ["c5", "c0", "c4", "c3"]


def test_empty_and_none_ids_use_the_positional_fallback() -> None:
    items = ensure_unique_ids([{"id": ""}, {"id": None}], "evidence").items
    assert [item["id"] for item in items] == ["evidence0", "evidence1"]
    items = ensure_unique_ids([{"id": "???"}], "evidence").items
    assert [item["id"] for item in items] == ["evidence0"]
    with pytest.raises(ToolError) as raised:
        ensure_unique_ids([{"id": ""}, {"id": None}, {"id": "???"}], "evidence")
    assert str(raised.value) == "IDs '' and '???' both sanitize to ''."


def test_id_keeps_its_key_position_or_is_appended() -> None:
    items = ensure_unique_ids([{"text": "t", "id": "a b", "extra": 1}, {"text": "u"}], "c").items
    assert list(items[0]) == ["text", "id", "extra"]
    assert list(items[1]) == ["text", "id"]


def test_inputs_are_not_mutated() -> None:
    original = {"id": "a/b", "text": "t"}
    ensure_unique_ids([original], "c")
    assert original == {"id": "a/b", "text": "t"}
