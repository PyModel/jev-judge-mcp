"""Python expectations for Sanctioned Divergences (`docs/reference/divergences.json`, ADR-0020).

The registry owns the facts: which fixture calls diverge, under which ADR, on which surface and
tier. This module owns only the expectation *functions*, keyed by fixture-call id — no divergence
fact is authored here. `DIVERGENT` is a projection of the registry, and the integrity test
(`tests/contract/test_divergence_registry.py`) proves the two never drift apart.
"""

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from jev_judge_mcp.providers.resolver import VERCEL_UNSUPPORTED
from jev_judge_mcp.serialize import stringify
from jev_judge_mcp.text import TRUNCATION_MARKER
from tests.parity.program_expect import expect_program

type Expectation = Callable[[list[Any], str, bool], tuple[list[Any], str, bool]]

REGISTRY_PATH = Path(__file__).parents[2] / "docs" / "reference" / "divergences.json"

REGISTRY: dict[str, Any] = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
"""The parsed registry: the single source of divergence truth (ADR-0020)."""


def _map_strings(value: Any, change: Callable[[str], str]) -> Any:
    if isinstance(value, str):
        return change(value)
    if isinstance(value, list):
        return [_map_strings(item, change) for item in value]  # pyright: ignore[reportUnknownVariableType]
    if isinstance(value, dict):
        return {key: _map_strings(item, change) for key, item in value.items()}  # pyright: ignore[reportUnknownVariableType]
    return value


def _drop_split_surrogate(bodies: list[Any], text: str, is_error: bool) -> tuple[list[Any], str, bool]:
    """ADR-0005: a cut that splits a surrogate pair drops the high surrogate instead of keeping it."""

    def fix(value: str) -> str:
        return value.replace("\ud83d" + TRUNCATION_MARKER, TRUNCATION_MARKER)

    return _map_strings(bodies, fix), text.replace("\\ud83d" + TRUNCATION_MARKER, TRUNCATION_MARKER), is_error


def _vercel_unsupported(bodies: list[Any], text: str, is_error: bool) -> tuple[list[Any], str, bool]:
    """ADR-0007: the vercel slot resolves, but Python reports it unsupported."""
    return bodies, VERCEL_UNSUPPORTED, is_error


def _refused_only_field(reason: str) -> Expectation:
    """ADR-0004: V8 accepts the one field's pattern; Python refuses it, so nothing is asked at all."""

    def expect(bodies: list[Any], text: str, is_error: bool) -> tuple[list[Any], str, bool]:
        recorded = json.loads(text)
        (field,) = recorded["results"]
        payload = {
            "tool": "jev_extract",
            "model": recorded["model"],
            "provider": "none",
            "summary": {"fields": 1, "extracted": 0, "auto": 0, "review": 0, "not_found": 0, "invalid": 1},
            "thresholds": recorded["thresholds"],
            "results": [
                {
                    "id": field["id"],
                    "value": None,
                    "status": "invalid_pattern",
                    "reason": reason,
                    "candidates_considered": 0,
                    "candidates_truncated": False,
                    "matches_skipped_too_long": 0,
                }
            ],
            "usage": None,
        }
        return [], stringify(payload), is_error

    return expect


def _reason(field: int, reason: str) -> Expectation:
    """ADR-0004: both refuse the pattern; the reason is Python's named one, not V8's message."""

    def expect(bodies: list[Any], text: str, is_error: bool) -> tuple[list[Any], str, bool]:
        recorded = json.loads(text)
        recorded["results"][field]["reason"] = reason
        return bodies, stringify(recorded), is_error

    return expect


def _same(bodies: list[Any], text: str, is_error: bool) -> tuple[list[Any], str, bool]:
    """Tagged, but Python reproduces the reference exactly (V8's own text for flags V8 rejects)."""
    return bodies, text, is_error


def _classify_generated_id_collision(bodies: list[Any], text: str, is_error: bool) -> tuple[list[Any], str, bool]:
    """ADR-0031: the reference returns two rows with the same id; Python rejects before the request.

    Items are checked first, so this recording fails on `item0` and never asks.
    """
    del bodies, text, is_error
    return [], "Duplicate item id: item0", True


_EXPECTATIONS: dict[str, Expectation] = {
    "invalid-pattern/extract-multiline-flag#0": _refused_only_field(
        "unsupported regular expression: the 'm' flag is outside the supported subset"
    ),
    "invalid-pattern/extract-unicode-flag#0": _refused_only_field(
        "unsupported regular expression: the 'u' flag is outside the supported subset"
    ),
    "invalid-pattern/extract-named-group#0": _refused_only_field(
        "unsupported regular expression: a named group is outside the supported subset"
    ),
    "invalid-pattern/extract-variable-lookbehind#0": _refused_only_field(
        "unsupported regular expression: variable-length lookbehind is outside the supported subset"
    ),
    "invalid-pattern/extract-syntax-error#0": _reason(0, "invalid regular expression: invalid group"),
    "invalid-pattern/extract-unknown-flag#0": _same,
    "unicode-astral/find-truncation-splits-surrogate-pair#0": _drop_split_surrogate,
    "unicode-astral/gate-claim-truncation-splits-surrogate#0": _drop_split_surrogate,
    "resolver-error/explicit-vercel-unset#0": _vercel_unsupported,
    "duplicate-id/classify-omitted-ids-get-positional-fallbacks#0": _classify_generated_id_collision,
}


def _chain(previous: Expectation) -> Expectation:
    def expect(bodies: list[Any], text: str, is_error: bool) -> tuple[list[Any], str, bool]:
        bodies, text, is_error = previous(bodies, text, is_error)
        return expect_program(bodies, text, is_error)

    return expect


for _entry in REGISTRY["divergences"]:
    if _entry["id"] == "program-response-fields":
        for _fid in _entry["fixtures"]:
            _previous = _EXPECTATIONS.get(_fid)
            _EXPECTATIONS[_fid] = expect_program if _previous is None else _chain(_previous)

_UNEXPECTED = sorted(
    {fid for divergence in REGISTRY["divergences"] for fid in divergence["fixtures"]} - _EXPECTATIONS.keys()
)
if _UNEXPECTED:
    raise RuntimeError(f"registry fixture calls without a Python expectation in divergences.py: {_UNEXPECTED}")

DIVERGENT: dict[str, Expectation] = {
    fid: _EXPECTATIONS[fid] for divergence in REGISTRY["divergences"] for fid in divergence["fixtures"]
}
