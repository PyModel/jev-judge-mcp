"""Extract's bare 0.01 sum tolerance rejects a 0.99 sum in float64 (Q2, ADR-0012)."""

from collections.abc import Iterable

from jev_judge_mcp.domain.answers import ChoiceAnswer, RawAnswer
from jev_judge_mcp.validation.choice import validate_choice

EXTRACT_SUM_TOLERANCE = 0.01


def validate_extract_choice(answer: RawAnswer, expected_keys: Iterable[str]) -> ChoiceAnswer | None:
    return validate_choice(answer, expected_keys, sum_tolerance=EXTRACT_SUM_TOLERANCE)
