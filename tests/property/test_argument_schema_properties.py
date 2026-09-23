"""Hypothesis properties for compiled argument schemas (ADR-0022).

If a schema compiles, every constraint it advertises can change a parse result: for each keyword,
a witness argument set parses differently (value or issue text) under the schema with and without
that keyword. Witnesses are built from the JSON Schema itself, not the compiled parser, so a
keyword the parser consumes but never enforces fails the property. A keyword this walk has no
witness for fails it too: it is a constraint the compiler accepted without a meaning for it.
"""

import copy
from typing import Any, cast

import pytest
from hypothesis import example, given
from hypothesis import strategies as st

from jev_judge_mcp.tools import TOOLS, JevTool
from jev_judge_mcp.tools.arguments import ArgumentsError, SchemaUnfaithful, compile_argument_schema

type Step = tuple[str, str | int]
type Pointer = tuple[Step, ...]

_ANNOTATIONS = frozenset({"$schema", "title", "description"})
_STRUCTURE = {
    "string": {"type", "minLength", "maxLength", "pattern"},
    "number": {"type", "minimum", "maximum"},
    "integer": {"type", "minimum", "maximum"},
    "boolean": {"type"},
    "array": {"type", "items", "minItems", "maxItems"},
    "object": {"type", "properties", "required", "additionalProperties"},
}
_CONSTRAINTS = frozenset({"minLength", "maxLength", "pattern", "minimum", "maximum", "minItems", "maxItems"})
_NOT_A_PATTERN = ("", "!", "a", "0", "\n")


def _constraints(schema: dict[str, Any], pointer: Pointer = ()) -> list[tuple[Pointer, str, str | None]]:
    """Every (node, keyword, required name) an accepted schema advertises; fails on a keyword with no witness."""
    keys = set(schema) - _ANNOTATIONS
    allowed: set[str] = {"anyOf"} if "anyOf" in schema else _STRUCTURE.get(schema.get("type", ""), set())
    if keys - allowed:
        pytest.fail(f"accepted keyword(s) {sorted(keys - allowed)} at {pointer} have no witness: nothing enforces them")
    found: list[tuple[Pointer, str, str | None]] = [(pointer, key, None) for key in sorted(keys & _CONSTRAINTS)]
    if schema.get("type") == "integer":
        found.append((pointer, "integer", None))
    found += [(pointer, "required", name) for name in schema.get("required", [])]
    for key, sub in schema.get("properties", {}).items():
        found += _constraints(sub, (*pointer, ("properties", key)))
    if "items" in schema:
        found += _constraints(schema["items"], (*pointer, ("items", 0)))
    for index, option in enumerate(schema.get("anyOf", [])):
        found += _constraints(option, (*pointer, ("anyOf", index)))
    return found


def _filler(schema: dict[str, Any]) -> object:
    """A value of the schema's type with every required key: it may dirty a parse but never aborts it."""
    if "anyOf" in schema:
        return _filler(schema["anyOf"][0])
    match schema["type"]:
        case "string":
            return "a"
        case "number" | "integer":
            return schema.get("minimum", 0)
        case "boolean":
            return False
        case "array":
            return []
        case _:
            return {name: _filler(schema["properties"][name]) for name in schema.get("required", [])}


def _violation(schema: dict[str, Any], keyword: str, name: str | None) -> object:
    match keyword:
        case "minLength":
            return ""
        case "maxLength":
            return "a" * (schema["maxLength"] + 1)
        case "pattern":
            compiled = compile_argument_schema("witness", _root({"type": "string", "pattern": schema["pattern"]}))
            for text in _NOT_A_PATTERN:
                try:
                    compiled({"p": text})
                except ArgumentsError:
                    return text
            pytest.fail(f"no candidate violates pattern {schema['pattern']!r}")
        case "minimum":
            return schema["minimum"] - 1
        case "maximum":
            return schema["maximum"] + 1
        case "integer":
            return 0.5
        case "minItems":
            return []
        case "maxItems":
            return [_filler(schema["items"])] * (schema["maxItems"] + 1)
        case _:
            record = cast(dict[str, object], _filler(schema))
            del record[cast(str, name)]
            return record


def _witness(schema: dict[str, Any], pointer: Pointer, keyword: str, name: str | None) -> object:
    """A value that violates `keyword` at `pointer`, reached without aborting any node on the way."""
    if not pointer:
        return _violation(schema, keyword, name)
    (kind, key), rest = pointer[0], pointer[1:]
    if kind == "properties":
        record = cast(dict[str, object], _filler(schema))
        return record | {key: _witness(schema["properties"][key], rest, keyword, name)}
    if kind == "items":
        return [_witness(schema["items"], rest, keyword, name)]
    return _witness(schema["anyOf"][key], rest, keyword, name)


def _without(schema: dict[str, Any], pointer: Pointer, keyword: str, name: str | None) -> dict[str, Any]:
    stripped = copy.deepcopy(schema)
    node = stripped
    for kind, key in pointer:
        node = node[kind] if kind == "items" else node[kind][key]
    if keyword == "integer":
        node["type"] = "number"
    elif keyword == "required":
        node["required"].remove(name)
    else:
        del node[keyword]
    return stripped


def _outcome(schema: dict[str, Any], arguments: object) -> tuple[str, str]:
    try:
        return "ok", repr(compile_argument_schema("jev_test", schema)(cast(dict[str, object], arguments)))
    except ArgumentsError as error:
        return "error", str(error)


def assert_every_constraint_participates(schema: dict[str, Any]) -> None:
    for pointer, keyword, name in _constraints(schema):
        arguments = _witness(schema, pointer, keyword, name)
        with_it, without_it = _outcome(schema, arguments), _outcome(_without(schema, pointer, keyword, name), arguments)
        assert with_it != without_it, f"{keyword} {name or ''} at {pointer} never affects the parse of {arguments!r}"
        assert with_it[0] == "error"


def _root(schema: dict[str, Any]) -> dict[str, Any]:
    return {"type": "object", "properties": {"p": schema}, "required": ["p"], "additionalProperties": False}


def _kind(schema: dict[str, Any]) -> str:
    kind: str = schema["type"]
    return "number" if kind == "integer" else kind


def _typed(
    kind: str, fixed: dict[str, st.SearchStrategy[Any]] | None = None, **choices: st.SearchStrategy[Any]
) -> st.SearchStrategy[dict[str, Any]]:
    """`{"type": kind}` with every `fixed` keyword plus any subset of `choices`."""
    return st.fixed_dictionaries({"type": st.just(kind)} | (fixed or {}), optional=choices)


_SCALARS: st.SearchStrategy[dict[str, Any]] = st.one_of(
    _typed(
        "string",
        minLength=st.integers(1, 4),
        maxLength=st.integers(0, 6),
        pattern=st.sampled_from(["^[a-z]*$", "^[0-9]+$", "^(?:ab|c)$"]),
    ),
    *(
        _typed(kind, minimum=st.integers(-3, 3) | st.just(0.5), maximum=st.integers(-3, 3) | st.just(2.5))
        for kind in ("number", "integer")
    ),
    _typed("boolean"),
    _typed("object", additionalProperties=st.just({})),
)


def _object(subs: dict[str, dict[str, Any]], required: list[str]) -> dict[str, Any]:
    return {"type": "object", "properties": subs, "required": required, "additionalProperties": False}


def _objects(children: st.SearchStrategy[dict[str, Any]]) -> st.SearchStrategy[dict[str, Any]]:
    def with_required(subs: dict[str, dict[str, Any]]) -> st.SearchStrategy[dict[str, Any]]:
        return st.builds(_object, st.just(subs), st.lists(st.sampled_from(sorted(subs)), unique=True))

    return st.dictionaries(st.sampled_from(["a", "b", "c"]), children, min_size=1).flatmap(with_required)


def _union(options: list[dict[str, Any]]) -> dict[str, Any]:
    return {"anyOf": options}


def _extend(children: st.SearchStrategy[dict[str, Any]]) -> st.SearchStrategy[dict[str, Any]]:
    typed = children.filter(lambda schema: "anyOf" not in schema)
    return st.one_of(
        _typed("array", {"items": children}, minItems=st.integers(1, 3), maxItems=st.integers(0, 4)),
        _objects(children),
        st.lists(typed, min_size=1, max_size=3, unique_by=_kind).map(_union),
    )


CLEAN = _objects(st.recursive(_SCALARS, _extend, max_leaves=8))

_NOISE: list[dict[str, Any]] = [
    {"type": "number"},
    {"type": "array"},
    {"minItems": 2},
    {"minLength": 1},
    {"maxLength": 3},
    {"pattern": "^a*$"},
    {"minimum": 0},
    {"items": {"type": "string"}},
    {"required": ["a"]},
    {"properties": {}},
    {"additionalProperties": False},
    {"anyOf": [{"type": "string"}]},
    {"enum": ["a"]},
    {"const": 1},
    {"description": "annotation"},
]


def _noisy(schema: dict[str, Any], noise: st.DataObject) -> dict[str, Any]:
    """`schema` with random extra keywords (never overwriting one it has) sprinkled on its nodes."""
    node: dict[str, Any] = {
        key: {name: _noisy(sub, noise) for name, sub in value.items()}
        if key == "properties"
        else _noisy(value, noise)
        if key == "items"
        else [_noisy(option, noise) for option in value]
        if key == "anyOf"
        else value
        for key, value in schema.items()
    }
    if noise.draw(st.booleans()):
        node = noise.draw(st.sampled_from(_NOISE)) | node
    return node


@given(CLEAN)
def test_every_constraint_of_a_generated_schema_affects_a_parse(schema: dict[str, Any]) -> None:
    assert_every_constraint_participates(schema)


@given(CLEAN, st.data())
@example(_root({"anyOf": [{"type": "string"}], "type": "number"}), None)
@example(_root({"anyOf": [{"type": "array", "items": {"type": "string"}}], "minItems": 2}), None)
def test_a_schema_either_fails_construction_or_every_constraint_affects_a_parse(
    schema: dict[str, Any], noise: st.DataObject | None
) -> None:
    candidate = schema if noise is None else _noisy(schema, noise)
    try:
        compile_argument_schema("jev_test", candidate)
    except SchemaUnfaithful:
        return
    assert_every_constraint_participates(candidate)


@pytest.mark.parametrize("tool", TOOLS, ids=lambda tool: tool.name)
def test_every_constraint_of_a_published_schema_affects_a_parse(tool: JevTool) -> None:
    assert_every_constraint_participates(tool.definition.input_schema)
