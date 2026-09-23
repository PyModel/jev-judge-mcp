"""Tool argument validation with the reference's observable behavior: zod 3 run by the TS MCP SDK 1.30.

The published `inputSchema` (draft-07, as `zod-to-json-schema` printed it) drives validation, so the
schema a client sees is the one enforced. `compile_argument_schema` turns it into an immutable
parser once, at `Toolset` construction (ADR-0022): every keyword is consumed by the node that
parses it, and a keyword nothing consumes fails construction instead of being silently ignored.

zod semantics that the JSON Schema does not state are reproduced here:

- Unknown object keys are stripped, not rejected, although the schema says `additionalProperties: false`.
- A parsed object holds its schema's keys in schema order, only those the caller sent.
- A failed check either aborts its value (wrong type, missing, failed union) or only dirties it
  (length, bounds, pattern, integer); an object or array with an aborted member is aborted. A union
  returns its first valid option, else the issues of its first dirty option, else `Invalid input`.
- Checks run in zod's order: integer, minimum, maximum; minLength, pattern, maxLength; array
  lengths before items. Lengths are UTF-16 units (ADR-0005).
- A non-finite number (`NaN` or an infinity) is rejected before those checks. `JSON.parse` never
  yields one; HTTP `from_json` does, and `nan < bound` is false, so a minimum or maximum would not.
- The SDK parses asynchronously. Union issues, and the refinements that run after a union, reach the
  error list after every synchronous issue of the whole call, so they are listed last.

The error text is the SDK's: `MCP error -32602: Input validation error: Invalid arguments for tool
{name}: {issues}`, one `{message} at {path}` per issue, joined by newlines (`zod-compat.js:122-166`).
"""

import math
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Final, Literal, NoReturn, cast

from jev_judge_mcp.serialize import number_to_string
from jev_judge_mcp.text import length

type Path = tuple[str | int, ...]
type Status = Literal["valid", "dirty", "aborted"]
type Schema = Mapping[str, Any]

INVALID_PARAMS: Final = -32602

_MISSING: Final = object()
"""A key the caller did not send: JS `undefined`, which zod reports as `Required`."""

_ANNOTATIONS: Final[frozenset[str]] = frozenset({"$schema", "title", "description"})


@dataclass(frozen=True, slots=True)
class Issue:
    message: str
    path: Path

    def render(self) -> str:
        """`getParseErrorMessage` for one issue: the bare message at the root, else `{message} at {path}`."""
        if not self.path:
            return self.message
        dotted = str(self.path[0])
        for segment in self.path[1:]:
            dotted += f"[{segment}]" if isinstance(segment, int) else f".{segment}"
        return f"{self.message} at {dotted}"


@dataclass(frozen=True, slots=True)
class Refinement:
    """A zod `.refine(check, {message})` on one top-level property; it runs on the parsed value unless aborted."""

    check: Callable[[object], bool]
    message: str


class ArgumentsError(Exception):
    """The arguments failed validation; `str()` is the SDK's tool-error text."""

    def __init__(self, tool: str, issues: Sequence[Issue]) -> None:
        rendered = "\n".join(issue.render() for issue in issues)
        super().__init__(
            f"MCP error {INVALID_PARAMS}: Input validation error: Invalid arguments for tool {tool}: {rendered}"
        )
        self.issues = list(issues)


class SchemaUnfaithful(Exception):
    """A published schema uses something the argument parser cannot faithfully enforce (ADR-0022)."""


@dataclass(slots=True)
class _Sink:
    issues: list[Issue] = field(default_factory=list[Issue])
    deferred: list[Issue] = field(default_factory=list[Issue])
    """Issues zod adds from a promise callback: after every synchronous issue of the parse."""

    def ordered(self) -> list[Issue]:
        return self.issues + self.deferred


def _received(value: object) -> str:
    """zod `getParsedType` over parsed JSON."""
    if value is _MISSING:
        return "undefined"
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int | float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    return "object"


def _invalid_type(expected: str, value: object, path: Path, sink: _Sink) -> tuple[Status, object]:
    received = _received(value)
    message = "Required" if received == "undefined" else f"Expected {expected}, received {received}"
    sink.issues.append(Issue(message, path))
    return "aborted", None


def _merge(status: Status, other: Status) -> Status:
    if "aborted" in (status, other):
        return "aborted"
    return "dirty" if "dirty" in (status, other) else "valid"


@dataclass(frozen=True, slots=True)
class _String:
    min_length: int | None
    pattern: re.Pattern[str] | None
    max_length: int | None

    def parse(self, value: object, path: Path, sink: _Sink) -> tuple[Status, object]:
        if not isinstance(value, str):
            return _invalid_type("string", value, path, sink)
        status: Status = "valid"
        units = length(value)
        if self.min_length is not None and units < self.min_length:
            sink.issues.append(Issue(f"String must contain at least {self.min_length} character(s)", path))
            status = "dirty"
        if self.pattern is not None and not self.pattern.search(value):
            sink.issues.append(Issue("Invalid", path))
            status = "dirty"
        if self.max_length is not None and units > self.max_length:
            sink.issues.append(Issue(f"String must contain at most {self.max_length} character(s)", path))
            status = "dirty"
        return status, value


@dataclass(frozen=True, slots=True)
class _Number:
    integer: bool
    minimum: int | float | None
    maximum: int | float | None

    def parse(self, value: object, path: Path, sink: _Sink) -> tuple[Status, object]:
        if isinstance(value, bool) or not isinstance(value, int | float):
            return _invalid_type("number", value, path, sink)
        if isinstance(value, float) and not math.isfinite(value):
            sink.issues.append(Issue("Number must be finite", path))
            return "dirty", value
        status: Status = "valid"
        if self.integer and isinstance(value, float) and not value.is_integer():
            sink.issues.append(Issue("Expected integer, received float", path))
            status = "dirty"
        if self.minimum is not None and value < self.minimum:
            bound = number_to_string(self.minimum)
            sink.issues.append(Issue(f"Number must be greater than or equal to {bound}", path))
            status = "dirty"
        if self.maximum is not None and value > self.maximum:
            bound = number_to_string(self.maximum)
            sink.issues.append(Issue(f"Number must be less than or equal to {bound}", path))
            status = "dirty"
        return status, value


@dataclass(frozen=True, slots=True)
class _Boolean:
    def parse(self, value: object, path: Path, sink: _Sink) -> tuple[Status, object]:
        return ("valid", value) if isinstance(value, bool) else _invalid_type("boolean", value, path, sink)


@dataclass(frozen=True, slots=True)
class _Array:
    items: "_Node"
    min_items: int | None
    max_items: int | None

    def parse(self, value: object, path: Path, sink: _Sink) -> tuple[Status, object]:
        if not isinstance(value, list):
            return _invalid_type("array", value, path, sink)
        items = cast(list[object], value)
        status: Status = "valid"
        if self.min_items is not None and len(items) < self.min_items:
            sink.issues.append(Issue(f"Array must contain at least {self.min_items} element(s)", path))
            status = "dirty"
        if self.max_items is not None and len(items) > self.max_items:
            sink.issues.append(Issue(f"Array must contain at most {self.max_items} element(s)", path))
            status = "dirty"
        parsed: list[object] = []
        for index, item in enumerate(items):
            item_status, item_value = self.items.parse(item, (*path, index), sink)
            status = _merge(status, item_status)
            parsed.append(item_value)
        return status, parsed


@dataclass(frozen=True, slots=True)
class _Property:
    key: str
    node: "_Node"
    required: bool
    refinement: Refinement | None


@dataclass(frozen=True, slots=True)
class _Object:
    properties: tuple[_Property, ...]

    def parse(self, value: object, path: Path, sink: _Sink) -> tuple[Status, object]:
        if not isinstance(value, dict):
            return _invalid_type("object", value, path, sink)
        record = cast(dict[str, object], value)
        status: Status = "valid"
        parsed: dict[str, object] = {}
        for prop in self.properties:
            item = record.get(prop.key, _MISSING)
            if item is _MISSING and not prop.required:
                continue
            item_status, item_value = prop.node.parse(item, (*path, prop.key), sink)
            if prop.refinement is not None and item_status != "aborted" and not prop.refinement.check(item_value):
                sink.deferred.append(Issue(prop.refinement.message, (*path, prop.key)))
                item_status = "dirty"
            status = _merge(status, item_status)
            parsed[prop.key] = item_value
        return status, parsed


@dataclass(frozen=True, slots=True)
class _Record:
    """z.record(z.any()): any object, kept whole."""

    def parse(self, value: object, path: Path, sink: _Sink) -> tuple[Status, object]:
        if not isinstance(value, dict):
            return _invalid_type("object", value, path, sink)
        return "valid", dict(cast(dict[str, object], value))


@dataclass(frozen=True, slots=True)
class _Union:
    options: tuple["_Node", ...]

    def parse(self, value: object, path: Path, sink: _Sink) -> tuple[Status, object]:
        results: list[tuple[Status, object, _Sink]] = []
        for option in self.options:
            option_sink = _Sink()
            option_status, option_value = option.parse(value, path, option_sink)
            if option_status == "valid":
                return option_status, option_value
            results.append((option_status, option_value, option_sink))
        for option_status, option_value, option_sink in results:
            if option_status == "dirty":
                sink.deferred.extend(option_sink.ordered())
                return option_status, option_value
        sink.deferred.append(Issue("Invalid input", path))
        return "aborted", None


type _Node = _String | _Number | _Boolean | _Array | _Object | _Record | _Union


@dataclass(frozen=True, slots=True)
class ArgumentParser:
    """A tool's compiled argument schema. Calling it returns the parsed arguments or raises `ArgumentsError`."""

    tool: str
    root: _Object

    def __call__(self, arguments: Mapping[str, object] | None) -> dict[str, object]:
        """The parsed arguments, or `ArgumentsError` with every issue in the order the reference reports them.

        `None` is arguments the caller did not send (JS `undefined`), reported at the root as `Required`.
        """
        sink = _Sink()
        status, value = self.root.parse(_MISSING if arguments is None else arguments, (), sink)
        if status != "valid":
            raise ArgumentsError(self.tool, sink.ordered())
        return cast(dict[str, object], value)


def compile_argument_schema(
    tool: str, schema: Schema, refinements: Mapping[str, Refinement] | None = None
) -> ArgumentParser:
    """Compile `schema` into its parser, or raise `SchemaUnfaithful` naming the tool and schema path.

    The root is an object with properties; each refinement must name one of them. Every other
    keyword is consumed by the node that enforces it; a leftover keyword is a construction failure.
    """
    compiler = _Compiler(tool)
    root = compiler.node(schema, "")
    if not isinstance(root, _Object):
        compiler.fail("", "the root must be an object with properties")
    refinements = refinements or {}
    keys = {prop.key for prop in root.properties}
    for key in refinements:
        if key not in keys:
            compiler.fail("", f"refinement {key!r} names no root property")
    properties = tuple(
        _Property(prop.key, prop.node, prop.required, refinements.get(prop.key)) for prop in root.properties
    )
    return ArgumentParser(tool, _Object(properties))


def parse_arguments(
    tool: str,
    schema: Schema,
    arguments: Mapping[str, object] | None,
    refinements: Mapping[str, Refinement] | None = None,
) -> dict[str, object]:
    """Compile `schema` and parse `arguments` once; a served tool calls the parser its `Toolset` compiled."""
    return compile_argument_schema(tool, schema, refinements)(arguments)


def _js_pattern(pattern: str) -> re.Pattern[str]:
    """The schema's JS regex. Its `$` is the end of input: Python's would also match before a final newline."""
    if not pattern.startswith("^") or not pattern.endswith("$") or not pattern.isascii():
        raise ValueError(f"Unsupported schema pattern {pattern!r}.")
    return re.compile(r"\A" + pattern[1:-1] + r"\Z")


def _kind(node: _Node) -> str:
    """The zod type a node's value must have: number and integer share one, as do object and record."""
    match node:
        case _Number():
            return "number"
        case _Object() | _Record():
            return "object"
        case _:
            return type(node).__name__


@dataclass(frozen=True, slots=True)
class _Compiler:
    tool: str

    def fail(self, path: str, why: str) -> NoReturn:
        raise SchemaUnfaithful(f"{self.tool} inputSchema at {path or 'root'}: {why}")

    def node(self, schema: object, path: str) -> _Node:
        if not isinstance(schema, Mapping):
            self.fail(path, "a schema must be an object")
        rest = {key: value for key, value in cast(Schema, schema).items() if key not in _ANNOTATIONS}
        node = self._consume(rest, path)
        if rest:
            self.fail(path, f"unsupported keyword(s) {sorted(rest)}: the parser would not enforce them")
        return node

    def _consume(self, rest: dict[str, Any], path: str) -> _Node:
        if "anyOf" in rest:
            return self._union(rest.pop("anyOf"), path)
        if "type" not in rest:
            self.fail(path, "neither a type nor anyOf: the parser would accept anything here")
        match rest.pop("type"):
            case "string":
                pattern = rest.pop("pattern", _MISSING)
                return _String(
                    self._count(rest, "minLength", path, 1),
                    None if pattern is _MISSING else self._pattern(pattern, path),
                    self._count(rest, "maxLength", path, 0),
                )
            case ("number" | "integer") as kind:
                minimum, maximum = self._bound(rest, "minimum", path), self._bound(rest, "maximum", path)
                return _Number(kind == "integer", minimum, maximum)
            case "boolean":
                return _Boolean()
            case "array":
                if "items" not in rest:
                    self.fail(path, "an array schema needs items")
                items = rest.pop("items")
                if not isinstance(items, Mapping):
                    self.fail(path, "items must be a single schema (draft-07 tuple form is not supported)")
                return _Array(
                    self.node(cast(Schema, items), f"{path}[]"),
                    self._count(rest, "minItems", path, 1),
                    self._count(rest, "maxItems", path, 0),
                )
            case "object":
                return self._object(rest, path)
            case other:
                self.fail(path, f"unsupported type {other!r}")

    def _object(self, rest: dict[str, Any], path: str) -> _Object | _Record:
        additional = rest.pop("additionalProperties", _MISSING)
        if "properties" not in rest:
            if not (additional is _MISSING or additional is True or additional == {}):
                self.fail(path, "additionalProperties constraint on a keep-whole object: the parser would ignore it")
            return _Record()
        if additional is not False:
            self.fail(path, "additionalProperties must be false: unknown keys are stripped, matching the reference")
        properties = rest.pop("properties")
        if not isinstance(properties, Mapping):
            self.fail(path, "properties must be an object")
        subs = cast(Schema, properties)
        required: object = rest.pop("required", [])
        if not isinstance(required, list) or not all(isinstance(name, str) for name in cast(list[object], required)):
            self.fail(path, "required must be a list of property names")
        names = cast(list[str], required)
        for name in names:
            if name not in subs:
                self.fail(path, f"required key {name!r} has no property")
        return _Object(
            tuple(
                _Property(key, self.node(sub, f"{path}.{key}" if path else key), key in names, None)
                for key, sub in subs.items()
            )
        )

    def _union(self, options: object, path: str) -> _Union:
        if not isinstance(options, list) or not options:
            self.fail(path, "anyOf must be a non-empty list of schemas")
        nodes: list[_Node] = []
        for index, option in enumerate(cast(list[object], options)):
            node = self.node(option, f"{path}/anyOf[{index}]")
            if isinstance(node, _Union):
                self.fail(path, f"anyOf[{index}] is itself a union: flatten it")
            if any(_kind(node) == _kind(earlier) for earlier in nodes):
                self.fail(path, f"anyOf[{index}] shares its type with an earlier option, which would shadow it")
            nodes.append(node)
        return _Union(tuple(nodes))

    def _pattern(self, pattern: object, path: str) -> re.Pattern[str]:
        if not isinstance(pattern, str):
            self.fail(path, "pattern must be a string")
        try:
            return _js_pattern(pattern)
        except (ValueError, re.error) as error:
            self.fail(path, str(error))

    def _count(self, rest: dict[str, Any], key: str, path: str, floor: int) -> int | None:
        """A length bound. A lower bound of 0 can never fail, so it must be at least 1."""
        value = rest.pop(key, _MISSING)
        if value is _MISSING:
            return None
        if isinstance(value, bool) or not isinstance(value, int) or value < floor:
            self.fail(path, f"{key} must be an integer of at least {floor}")
        return value

    def _bound(self, rest: dict[str, Any], key: str, path: str) -> int | float | None:
        value = rest.pop(key, _MISSING)
        if value is _MISSING:
            return None
        if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
            self.fail(path, f"{key} must be a finite number")
        return value
