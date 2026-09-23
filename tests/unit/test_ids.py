"""`sanitize_id` and `ensure_unique_ids` (`lib.ts:13-45`)."""

from jev_judge_mcp.ids import ensure_unique_ids, sanitize_id


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


def test_ensure_unique_ids_assigns_fallbacks_and_resolves_collisions() -> None:
    items, renamed = ensure_unique_ids(
        [{"id": "src/lib.ts", "text": "a"}, {"id": "src/lib.ts", "text": "b"}, {"text": "c"}],
        "candidate",
    )
    assert [item["id"] for item in items] == ["src_lib.ts", "src_lib.ts_1", "candidate2"]
    assert renamed == {"src/lib.ts": "src_lib.ts_1"}


def test_renamed_keeps_first_insertion_position_with_last_value() -> None:
    _, renamed = ensure_unique_ids([{"id": "a/b"}, {"id": "x"}, {"id": "a/b"}], "c")
    assert list(renamed.items()) == [("a/b", "a_b_1")]


def test_fallback_ids_collide_like_any_other() -> None:
    items, renamed = ensure_unique_ids([{"text": "no id"}, {"id": "item0"}], "item")
    assert [item["id"] for item in items] == ["item0", "item0_1"]
    assert renamed == {"item0": "item0_1"}


def test_suffix_search_skips_taken_suffixes() -> None:
    items, _ = ensure_unique_ids([{"id": "a"}, {"id": "a_1"}, {"id": "a"}], "c")
    assert [item["id"] for item in items] == ["a", "a_1", "a_2"]


def test_empty_and_none_ids_use_the_positional_fallback() -> None:
    items, renamed = ensure_unique_ids([{"id": ""}, {"id": None}, {"id": "???"}], "evidence")
    assert [item["id"] for item in items] == ["evidence0", "evidence1", "evidence2"]
    assert renamed == {"???": "evidence2"}


def test_id_keeps_its_key_position_or_is_appended() -> None:
    items, _ = ensure_unique_ids([{"text": "t", "id": "a b", "extra": 1}, {"text": "u"}], "c")
    assert list(items[0]) == ["text", "id", "extra"]
    assert list(items[1]) == ["text", "id"]


def test_inputs_are_not_mutated() -> None:
    original = {"id": "a/b", "text": "t"}
    ensure_unique_ids([original], "c")
    assert original == {"id": "a/b", "text": "t"}
