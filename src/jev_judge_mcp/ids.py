"""Caller ID validation and allocation using the reference sanitizer."""

import re
from collections.abc import Collection, Iterable, Mapping, Sequence
from itertools import count
from typing import NamedTuple

from jev_judge_mcp.limits import SANITIZE_ID_UNITS

_UNSAFE = re.compile(r"[^A-Za-z0-9_.-]+")
MAX_ID_LENGTH = SANITIZE_ID_UNITS
"""Kept as the reference's name for the bound `lib.ts` hardcodes; the value is owned by `limits.py`."""


def sanitize_id(raw: str) -> str:
    """Collapse each run of characters outside `[A-Za-z0-9_.-]` to `_`, strip edge underscores, keep 64.

    The result is ASCII, so code points and UTF-16 units coincide. The 64-unit slice runs after the
    strip, so a cut id may end in `_`, as in the reference.
    """
    return _UNSAFE.sub("_", raw).strip("_")[:MAX_ID_LENGTH]


class UniqueIds(NamedTuple):
    items: list[dict[str, object]]

    @property
    def ids(self) -> list[str]:
        return [str(item["id"]) for item in self.items]


def ensure_unique_ids(
    items: Sequence[Mapping[str, object]], fallback_prefix: str, *, reserved: Collection[str] = ()
) -> UniqueIds:
    """Check supplied IDs before allocating positional IDs in the remaining namespace."""
    # The tools package imports its handlers, which use this policy.
    from jev_judge_mcp.tools.base import ToolError

    supplied: dict[str, str] = {}
    keys: list[str] = []
    for item in items:
        raw = item.get("id")
        key = sanitize_id(raw) if isinstance(raw, str) else ""
        if isinstance(raw, str):
            if key in reserved:
                raise ToolError(f"ID {raw!r} sanitizes to reserved ID {key!r}.", code="invalid_arguments")
            if key in supplied:
                raise ToolError(
                    f"IDs {supplied[key]!r} and {raw!r} both sanitize to {key!r}.", code="invalid_arguments"
                )
            supplied[key] = raw
        keys.append(key)

    used = set(supplied).union(reserved)
    overflow = (f"{fallback_prefix}{index}" for index in count(len(items)))
    for index, key in enumerate(keys):
        if not key:
            positional = f"{fallback_prefix}{index}"
            keys[index] = positional if positional not in used else next(key for key in overflow if key not in used)
    return UniqueIds([{**item, "id": key} for item, key in zip(items, keys, strict=True)])


def generated_ids(labels: Iterable[str]) -> list[str]:
    """Allocate IDs for implicit file evidence, including repeated or aliased paths."""
    keys = [sanitize_id(label) for label in labels]
    used = set(keys)
    seen: set[str] = set()
    for index, key in enumerate(keys):
        if key in seen:
            suffixes = (f"_{n}" for n in count(1))
            keys[index] = next(
                candidate
                for suffix in suffixes
                if (candidate := key[: MAX_ID_LENGTH - len(suffix)] + suffix) not in used
            )
            used.add(keys[index])
        seen.add(key)
    return keys
