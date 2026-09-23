"""Noul answer validation (`validateNoulAnswer`, `index.ts:1200-1205`)."""

from jev_judge_mcp.domain.answers import RawAnswer
from jev_judge_mcp.domain.json import is_json_object
from jev_judge_mcp.validation.numbers import unit_interval


def validate_noul(answer: RawAnswer) -> float | None:
    """The `noul` probability if it is a finite number in [0, 1], else `None`.

    `0.0` is a valid answer: test the result with `is None`, never for truthiness.
    """
    if not is_json_object(answer):
        return None
    return unit_interval(answer.get("noul"))
