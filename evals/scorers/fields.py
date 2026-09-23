"""Tolerant readers for tool result JSON: a missing or malformed field reads as absent, never raises."""

from collections.abc import Mapping
from typing import Any, cast

Json = Mapping[str, Any]


def is_object(value: object) -> bool:
    return isinstance(value, Mapping)


def as_object(value: object) -> Json:
    """`value` when it is a JSON object, else an empty one."""
    return cast(Json, value) if isinstance(value, Mapping) else {}


def as_objects(value: object) -> list[Json]:
    """The JSON objects in `value` when it is a list, else none."""
    items = cast(list[object], value) if isinstance(value, list) else []
    return [cast(Json, item) for item in items if isinstance(item, Mapping)]


def as_number(value: object) -> float | None:
    """A JSON number as float; `bool` is not a number, as in JS."""
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else None


def by_id(output: Json, key: str) -> dict[str, Json]:
    """`output[key]` items keyed by their `id`."""
    return {str(item.get("id")): item for item in as_objects(output.get(key))}
