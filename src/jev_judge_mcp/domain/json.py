"""JSON value types, JSON text read as `JSON.parse` reads it, and parsed-JSON value-shape semantics (ADR-0019).

Beyond decoding, this module answers exactly two kinds of question about a parsed JSON value: what
JS `typeof` would say, and what shape it is. Nothing else belongs here — no answer semantics, no
provider rules, no normalization; those live above, in `validation/` and `providers/`.
"""

import json
import math
from collections.abc import Mapping, Sequence
from typing import TypeGuard

type JsonValue = str | int | float | bool | Sequence[JsonValue] | Mapping[str, JsonValue] | None


def decode_json(text: str) -> object:
    """`JSON.parse(text)`, raising `ValueError` where it throws.

    `json.loads` also reads `NaN`, `Infinity`, and `-Infinity`, which `JSON.parse` rejects, so those
    are refused here. A lone surrogate escape is kept as one code point, as `JSON.parse` keeps it.
    Nesting too deep for Python's recursion limit is a `ValueError` too.
    """
    try:
        return json.loads(text, parse_constant=_reject_constant, parse_int=_parse_int)
    except RecursionError as error:
        raise ValueError("JSON nesting is too deep") from error


def _reject_constant(name: str) -> object:
    raise ValueError(f"{name} is not JSON")


def _parse_int(digits: str) -> int | float:
    """An integer literal as `JSON.parse` reads it: exact while Python converts it, a double past
    Python's digit limit (`sys.get_int_max_str_digits`), where `JSON.parse` has long since rounded
    to the nearest double or infinity. The limit is a `ValueError` in `int()`, never JSON syntax."""
    try:
        return int(digits)
    except ValueError:
        return float(digits)


def is_json_object(value: object) -> TypeGuard[dict[str, object]]:
    """A JSON object: not null, not an array, not a primitive (`index.ts:1170-1172`)."""
    return isinstance(value, dict)


def as_number(value: object) -> float | None:
    """The float64 a JS `number` would hold, or `None` when `typeof value !== "number"`.

    `bool` is an `int` in Python but a boolean in JS, so it is not a number. An integer too large
    for a double becomes infinity, as `JSON.parse` would make it.
    """
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    try:
        return float(value)
    except OverflowError:
        return math.inf if value > 0 else -math.inf
