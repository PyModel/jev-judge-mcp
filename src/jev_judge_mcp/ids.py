"""`sanitize_id` and `ensure_unique_ids` (`lib.ts:13-45`)."""

import re
from collections.abc import Mapping, Sequence
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
    """Copies of the input items with `id` set; an existing `id` keeps its key position."""
    renamed: dict[str, str]
    """Caller id → id used, for every non-empty caller id that changed."""


def ensure_unique_ids(items: Sequence[Mapping[str, object]], fallback_prefix: str) -> UniqueIds:
    """Give every item a safe, unique id: sanitized, else `{fallback_prefix}{index}`; collisions get `_1`, `_2`, ….

    A missing or null `id` counts as empty. Fallback ids are not checked against later caller ids
    beyond the shared collision loop, exactly as in the reference.
    """
    used: set[str] = set()
    renamed: dict[str, str] = {}
    out: list[dict[str, object]] = []
    for index, item in enumerate(items):
        raw = item.get("id")
        raw_id = raw if isinstance(raw, str) else ""
        base = sanitize_id(raw_id) or f"{fallback_prefix}{index}"
        new_id = base
        suffix = 1
        while new_id in used:
            new_id = f"{base}_{suffix}"
            suffix += 1
        used.add(new_id)
        if raw_id and raw_id != new_id:
            renamed[raw_id] = new_id
        out.append({**item, "id": new_id})
    return UniqueIds(out, renamed)
