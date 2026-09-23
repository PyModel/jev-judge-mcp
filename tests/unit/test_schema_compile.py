"""ADR-0022: a published schema compiles into an immutable parser, or construction fails.

The real `TOOLS` compile. Anything the parser would ignore or reject at call time (a keyword no
node consumes, a sibling beside `anyOf`, an array without `items`, a refinement on no property)
fails construction with the tool name and schema path.
"""

import dataclasses
from typing import Any

import pytest
from mcp.types import Tool

from jev_judge_mcp.tools import TOOLS, JevTool, Toolset
from jev_judge_mcp.tools.arguments import ArgumentsError, Refinement, SchemaUnfaithful, compile_argument_schema

SCHEMA = {"type": "object", "properties": {"text": {"type": "string", "minLength": 1}}, "required": ["text"]}


def rooted(schema: object) -> dict[str, Any]:
    return {"type": "object", "properties": {"p": schema}, "required": ["p"], "additionalProperties": False}


@pytest.mark.parametrize("tool", TOOLS, ids=lambda tool: tool.name)
def test_every_published_schema_compiles(tool: JevTool) -> None:
    compile_argument_schema(tool.name, tool.definition.input_schema, tool.refinements)


@pytest.mark.parametrize(
    ("schema", "why"),
    [
        ({"anyOf": [{"type": "string"}], "type": "number"}, "['type']"),
        ({"anyOf": [{"type": "array", "items": {"type": "string"}}], "minItems": 2}, "['minItems']"),
        ({"type": "number", "minLength": 3}, "['minLength']"),
        ({"type": "string", "items": {"type": "string"}}, "['items']"),
        ({"type": "boolean", "pattern": "^a$"}, "['pattern']"),
        ({"type": "object", "additionalProperties": {}, "required": []}, "['required']"),
        ({"type": "array"}, "needs items"),
        ({"type": "array", "items": [{"type": "string"}]}, "tuple"),
        ({"type": "string", "enum": ["a", "b"]}, "['enum']"),
        ({"oneOf": [{"type": "string"}]}, "neither a type"),
        ({"type": "string", "pattern": "abc"}, "Unsupported schema pattern"),
        ({"type": "string", "pattern": r"^\ulégal$"}, "Unsupported schema pattern"),
        ({"type": "string", "pattern": "^($"}, "missing )"),
        ({"type": "string", "pattern": 5}, "pattern must be a string"),
        ({"type": "string", "minLength": 0}, "minLength must be an integer of at least 1"),
        ({"type": "array", "items": {"type": "string"}, "minItems": 0}, "minItems must be"),
        ({"type": "string", "maxLength": -1}, "maxLength must be"),
        ({"type": "string", "maxLength": True}, "maxLength must be"),
        ({"type": "string", "minLength": None}, "minLength must be"),
        ({"type": "number", "minimum": float("nan")}, "minimum must be a finite number"),
        ({"type": "number", "maximum": "1"}, "maximum must be a finite number"),
        ({"type": "object", "properties": {}, "required": ["ghost"], "additionalProperties": False}, "no property"),
        ({"type": "object", "properties": {}, "additionalProperties": True}, "must be false"),
        ({"type": "object", "properties": {}}, "must be false"),
        ({"type": "object", "additionalProperties": 1}, "keep-whole"),
        ({"type": "object", "additionalProperties": {"type": "string"}}, "keep-whole"),
        ({"anyOf": []}, "non-empty"),
        ({"anyOf": [{"type": "string"}, {"type": "string", "minLength": 1}]}, "shares its type"),
        ({"anyOf": [{"type": "integer"}, {"type": "number"}]}, "shares its type"),
        ({"anyOf": [{"type": "object"}, SCHEMA | {"additionalProperties": False}]}, "shares"),
        ({"anyOf": [{"anyOf": [{"type": "string"}]}]}, "flatten"),
        ({"type": "null"}, "unsupported type"),
        ({"type": ["string", "null"]}, "unsupported type"),
        ({"description": "nothing enforced"}, "neither a type"),
        ("string", "must be an object"),
    ],
)
def test_unfaithful_schema_fails_construction_with_a_reason(schema: object, why: str) -> None:
    with pytest.raises(SchemaUnfaithful) as caught:
        compile_argument_schema("jev_test", rooted(schema))
    assert str(caught.value).startswith("jev_test inputSchema at p")
    assert why in str(caught.value)


def test_the_violation_names_the_nested_path() -> None:
    nested = rooted({"anyOf": [{"type": "string", "const": "x"}]})
    with pytest.raises(SchemaUnfaithful) as caught:
        compile_argument_schema("jev_test", nested)
    assert "at p/anyOf[0]: unsupported keyword(s) ['const']" in str(caught.value)


@pytest.mark.parametrize(
    "schema",
    [{"type": "string"}, {"anyOf": [SCHEMA | {"additionalProperties": False}]}, {"type": "object"}],
    ids=["string", "union", "record"],
)
def test_the_root_is_an_object_with_properties(schema: dict[str, Any]) -> None:
    with pytest.raises(SchemaUnfaithful, match="at root: the root must be an object with properties"):
        compile_argument_schema("jev_test", schema)


def test_a_refinement_must_name_a_root_property() -> None:
    refinement = Refinement(lambda _: False, "refused")
    with pytest.raises(SchemaUnfaithful, match="refinement 'ghost' names no root property"):
        compile_argument_schema("jev_test", rooted({"type": "string"}), {"ghost": refinement})


def test_toolset_construction_fails_on_an_unfaithful_schema() -> None:
    async def handler(*_: object) -> Any:
        raise AssertionError

    definition = Tool.model_validate({"name": "jev_bad", "inputSchema": rooted({"type": "array"})})
    with pytest.raises(SchemaUnfaithful, match="jev_bad inputSchema at p: an array schema needs items"):
        Toolset(None, [JevTool(definition=definition, handler=handler)])  # type: ignore[arg-type]


def test_the_parser_is_immutable_and_independent_of_its_source() -> None:
    schema = rooted({"type": "string", "minLength": 2, "pattern": "^[a-z]+$"})
    parse = compile_argument_schema("jev_test", schema)
    schema["properties"]["p"]["minLength"] = 99
    schema["required"].clear()
    assert parse({"p": "ab"}) == {"p": "ab"}
    with pytest.raises(ArgumentsError, match="Required at p"):
        parse({})
    with pytest.raises(dataclasses.FrozenInstanceError):
        parse.root = parse.root  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        parse.root.properties[0].node.min_length = 0  # type: ignore[union-attr]
