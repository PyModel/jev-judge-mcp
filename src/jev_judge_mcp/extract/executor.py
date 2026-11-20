"""The worker's matcher and its results, projected to tool outcomes by `extract/candidates.py`."""

import re
from dataclasses import dataclass
from enum import Enum

from jev_judge_mcp.extract.dialect import Translated


@dataclass(frozen=True, slots=True)
class Matches:
    """The kept matches, in unit space (`dialect.from_units` maps them back)."""

    candidates: list[str]
    truncated: bool
    too_long: int


@dataclass(frozen=True, slots=True)
class Timeout:
    """The deadline passed before a result; a process executor killed the run."""


@dataclass(frozen=True, slots=True)
class Saturated:
    """Admission refused at the queue bound: no time was spent, nothing ran."""


class Unavailable(Enum):
    NO_RESULT = "no_result"
    """The worker exited without a result."""
    NOT_STARTED = "not_started"
    """The executor could not start somewhere to run the pattern."""


@dataclass(frozen=True, slots=True)
class Invalid:
    """A compiler diagnostic or an unavailable executor."""

    cause: str | Unavailable


type MatchResult = Matches | Timeout | Saturated | Invalid


def match_all(pattern: Translated, units: str, max_candidates: int, max_units: int) -> Matches | Invalid:
    """`document.matchAll` with the reference's frozen pipeline (`index.ts:894-903`).

    Drop zero-length matches, dedup by value (first wins), skip and count matches over `max_units`,
    then stop at `max_candidates` kept, flagging truncation. After an empty match the scan moves one
    unit on, as `AdvanceStringIndex` does without `u`; `finditer` would retry the same position.
    Returns a compiler diagnostic if the pattern does not compile.
    """
    try:
        compiled = re.compile(pattern.source, pattern.flags)
    except (re.error, RecursionError, OverflowError) as error:
        return Invalid(str(error))
    seen: set[str] = set()
    candidates: list[str] = []
    truncated = False
    too_long = 0
    position = 0
    while position <= len(units):
        match = compiled.search(units, position)
        if match is None:
            break
        start, end = match.span()
        position = end if end > start else end + 1
        value = units[start:end]
        if not value or value in seen:
            continue
        seen.add(value)
        if len(value) > max_units:
            too_long += 1
            continue
        if len(candidates) >= max_candidates:
            truncated = True
            break
        candidates.append(value)
    return Matches(candidates, truncated, too_long)
