"""The `tools/list` comparison from ADR-0010.

Both sides are parsed JSON. Object keys are sorted recursively, then `name`, `title`,
`description`, `inputSchema`, and `execution` must be exactly equal per tool. Fields
outside that set (for example an SDK-generated `outputSchema`) are not compared.
Order is checked separately from field equality so a failure says which one broke.
"""

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from tests.support.stdio import REPO_ROOT

SNAPSHOT_PATH: Path = REPO_ROOT / "docs" / "reference" / "ts-0.5.0-tools-list.json"
COMPARED_FIELDS = ("name", "title", "description", "inputSchema", "execution")


def load_snapshot() -> list[dict[str, Any]]:
    tools: list[dict[str, Any]] = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))["tools"]
    return tools


def sort_keys(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: sort_keys(value[key]) for key in sorted(value)}  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]
    if isinstance(value, list):
        return [sort_keys(item) for item in value]  # pyright: ignore[reportUnknownVariableType]
    return value


def compared_view(tool: Mapping[str, Any]) -> dict[str, Any]:
    """The compared fields only, keys sorted. A missing field stays missing, never defaulted."""
    return sort_keys({field: tool[field] for field in COMPARED_FIELDS if field in tool})


def field_mismatches(expected: Sequence[Mapping[str, Any]], served: Sequence[Mapping[str, Any]]) -> list[str]:
    """Differences for each served tool against the snapshot tool of the same name."""
    by_name = {tool["name"]: compared_view(tool) for tool in expected}
    problems: list[str] = []
    for tool in served:
        name = tool.get("name")
        if name not in by_name:
            problems.append(f"{name!r}: not in the snapshot")
            continue
        want, got = by_name[name], compared_view(tool)
        for field in COMPARED_FIELDS:
            if want.get(field, "<absent>") != got.get(field, "<absent>"):
                problems.append(f"{name}: {field} differs")
    return problems


def order_mismatch(expected: Sequence[Mapping[str, Any]], served: Sequence[Mapping[str, Any]]) -> str | None:
    """Served tools must appear in snapshot order (registration order in the reference)."""
    served_names = [tool.get("name") for tool in served]
    expected_order = [tool["name"] for tool in expected if tool["name"] in served_names]
    if served_names != expected_order:
        return f"served order {served_names} != snapshot order {expected_order}"
    return None
