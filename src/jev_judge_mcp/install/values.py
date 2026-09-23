"""Narrow JSON values the installer reads. `isinstance(x, list)` is `list[Unknown]` under pyright."""

from typing import TypeGuard


def is_json_array(value: object) -> TypeGuard[list[object]]:
    return isinstance(value, list)
