"""Argument validation reproduces zod 3 through the TS MCP SDK 1.30: statuses, messages, paths, order.

Every expected text here was produced by zod 3.25.76 and the SDK's `getParseErrorMessage` from the
pinned reference's `node_modules`, with the same schemas declared in zod.
"""

from typing import Any

import pytest

from jev_judge_mcp.tools.arguments import ArgumentsError, Issue, Refinement, compile_argument_schema
from jev_judge_mcp.tools.common import EVIDENCE_SCHEMA

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "name": {"type": "string", "minLength": 1, "maxLength": 3},
        "id": {"type": "string", "pattern": "^[a-z][a-z0-9_-]*$", "maxLength": 4},
        "top_k": {"type": "integer", "minimum": 1, "maximum": 50},
        "ratio": {"type": "number", "minimum": 0, "maximum": 1},
        "flag": {"type": "boolean"},
        "tags": {"type": "array", "items": {"type": "string", "minLength": 1}, "minItems": 1, "maxItems": 2},
        "evidence": EVIDENCE_SCHEMA,
        "context": {"anyOf": [{"type": "string"}, {"type": "object", "additionalProperties": {}}]},
    },
    "required": ["name"],
    "additionalProperties": False,
}


PARSE = compile_argument_schema("jev_x", SCHEMA)


def issues(arguments: dict[str, Any], refinements: dict[str, Refinement] | None = None) -> str:
    with pytest.raises(ArgumentsError) as caught:
        compile_argument_schema("jev_x", SCHEMA, refinements)(arguments)
    prefix = "MCP error -32602: Input validation error: Invalid arguments for tool jev_x: "
    text = str(caught.value)
    assert text.startswith(prefix)
    return text.removeprefix(prefix)


def test_valid_arguments_keep_schema_order_and_drop_unknown_keys() -> None:
    parsed = PARSE({"ratio": 1, "extra": True, "name": "ab", "evidence": {"text": "t"}})
    assert list(parsed) == ["name", "ratio", "evidence"]
    assert parsed["evidence"] == {"text": "t"}


def test_nested_objects_are_stripped_in_schema_order() -> None:
    parsed: Any = PARSE({"name": "a", "evidence": [{"text": "t", "junk": 1, "id": "i"}]})
    assert parsed["evidence"] == [{"id": "i", "text": "t"}]
    assert list(parsed["evidence"][0]) == ["id", "text"]


def test_record_keeps_any_object() -> None:
    assert PARSE({"name": "a", "context": {"b": [1]}})["context"] == {"b": [1]}


def test_messages() -> None:
    assert issues({}) == "Required at name"
    assert issues({"name": None}) == "Expected string, received null at name"
    assert issues({"name": ""}) == "String must contain at least 1 character(s) at name"
    assert issues({"name": "abcd"}) == "String must contain at most 3 character(s) at name"
    assert issues({"name": "a", "id": "Ab"}) == "Invalid at id"
    assert issues({"name": "a", "id": "a\n"}) == "Invalid at id"
    assert issues({"name": "a", "id": "Abcde"}) == "Invalid at id\nString must contain at most 4 character(s) at id"
    assert issues({"name": "a", "top_k": 2.5}) == "Expected integer, received float at top_k"
    assert issues({"name": "a", "top_k": 0}) == "Number must be greater than or equal to 1 at top_k"
    assert issues({"name": "a", "top_k": True}) == "Expected number, received boolean at top_k"
    assert issues({"name": "a", "ratio": 1.5}) == "Number must be less than or equal to 1 at ratio"
    assert issues({"name": "a", "tags": []}) == "Array must contain at least 1 element(s) at tags"
    assert issues({"name": "a", "tags": ["a", "b", ""]}) == (
        "Array must contain at most 2 element(s) at tags\nString must contain at least 1 character(s) at tags[2]"
    )
    assert issues({"name": "a", "tags": "a"}) == "Expected array, received string at tags"
    assert issues({"name": "a", "context": []}) == "Invalid input at context"
    assert issues({"name": "a", "flag": "true"}) == "Expected boolean, received string at flag"


def test_integer_valued_float_is_an_integer() -> None:
    assert PARSE({"name": "a", "top_k": 5.0})["top_k"] == 5.0


def test_lengths_count_utf16_units() -> None:
    assert issues({"name": "ab😀"}) == "String must contain at most 3 character(s) at name"
    assert PARSE({"name": "a😀"})["name"] == "a😀"


def test_union_reports_its_first_dirty_option() -> None:
    assert issues({"name": "a", "evidence": []}) == "Array must contain at least 1 element(s) at evidence"
    assert issues({"name": "a", "evidence": {"id": 1, "text": "t"}}) == "Invalid input at evidence"
    assert issues({"name": "a", "evidence": [{"id": "x"}]}) == "Invalid input at evidence"


def test_union_issues_come_after_every_synchronous_issue() -> None:
    assert issues({"name": "", "evidence": 5, "ratio": 2}) == (
        "String must contain at least 1 character(s) at name\n"
        "Number must be less than or equal to 1 at ratio\n"
        "Invalid input at evidence"
    )


def test_refinement_runs_on_valid_and_dirty_values_after_the_union() -> None:
    never = {"evidence": Refinement(lambda _: False, "refused")}
    assert issues({"name": "a", "evidence": "x"}, never) == "refused at evidence"
    assert issues({"name": "a", "evidence": []}, never) == (
        "Array must contain at least 1 element(s) at evidence\nrefused at evidence"
    )
    assert issues({"name": "a", "evidence": 5}, never) == "Invalid input at evidence"


def test_root_issue_renders_bare() -> None:
    assert Issue("Required", ()).render() == "Required"
    assert Issue("m", ("a", 0, "b")).render() == "m at a[0].b"


def test_missing_arguments_are_required_at_the_root() -> None:
    with pytest.raises(ArgumentsError) as caught:
        PARSE(None)
    assert str(caught.value).endswith("Invalid arguments for tool jev_x: Required")
