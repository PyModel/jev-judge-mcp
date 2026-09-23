"""Semantic number validators: unit intervals and confidence (ADR-0002, ADR-0019, ADR-0043).

Value-shape primitives (`as_number`, `is_json_object`) live in `domain/json.py`.
"""

import math

from jev_judge_mcp.domain.answers import ConfidenceKind
from jev_judge_mcp.domain.json import as_number


def unit_interval(value: object) -> float | None:
    """`value` when it is a finite number in [0, 1], else `None`. `-0.0` passes, as `-0 >= 0` in JS."""
    number = as_number(value)
    if number is None or not math.isfinite(number) or number < 0 or number > 1:
        return None
    return number


def confidence_kind(answer: dict[str, object]) -> ConfidenceKind:
    """Absent when missing or JSON null, number when a finite value in [0, 1], else malformed.

    `confidence_of` collapses absent and malformed to `None`. Callers that must tell them apart
    (Q4 on jev_verify) read this instead of the raw answer.
    """
    if answer.get("confidence") is None:
        return "absent"
    if unit_interval(answer["confidence"]) is None:
        return "malformed"
    return "number"


def confidence_of(answer: dict[str, object]) -> float | None:
    """A finite confidence in [0, 1], else `None`: unknown, never zero."""
    return unit_interval(answer.get("confidence"))
