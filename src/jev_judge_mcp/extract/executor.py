"""The regex execution port (ADR-0016): an executor compiles and matches a translated pattern.

`RegexExecutor.find` runs one translated pattern over unit-space text under an absolute monotonic
deadline and answers with a `MatchResult`: `Matches`, or one of three plain refusals — `Timeout`
(the deadline passed), `Saturated` (admission refused at the queue bound, ADR-0025), `Invalid` (no
result: the pattern did not compile or the executor could not produce one). Results carry no
caller-facing text; `extract/candidates.py` owns what each one tells the caller.

Two adapters: `worker.ProcessRegexExecutor`, killable worker processes for production, and
`InProcessRegexExecutor` here, which cannot stop a runaway match and exists for trusted corpora
such as the Node differential test.
"""

import re
from dataclasses import dataclass
from enum import Enum
from time import monotonic
from typing import Protocol

from jev_judge_mcp.extract.dialect import Translated
from jev_judge_mcp.limits import EXTRACT, ExtractCaps


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
    """The run ended without a result: the pattern did not compile, or its worker exited."""
    NOT_STARTED = "not_started"
    """The executor could not start somewhere to run the pattern."""


@dataclass(frozen=True, slots=True)
class Invalid:
    cause: Unavailable


type MatchResult = Matches | Timeout | Saturated | Invalid


class RegexExecutor(Protocol):
    async def find(self, pattern: Translated, text: str, *, deadline: float) -> MatchResult:
        """Match `pattern` over unit-space `text`, finishing by monotonic `deadline`."""
        ...

    async def aclose(self) -> None: ...


def match_all(pattern: Translated, units: str, max_candidates: int, max_units: int) -> Matches:
    """`document.matchAll` with the reference's frozen pipeline (`index.ts:894-903`).

    Drop zero-length matches, dedup by value (first wins), skip and count matches over `max_units`,
    then stop at `max_candidates` kept, flagging truncation. After an empty match the scan moves one
    unit on, as `AdvanceStringIndex` does without `u`; `finditer` would retry the same position.
    Raises `re.error` if the pattern does not compile.
    """
    compiled = re.compile(pattern.source, pattern.flags)
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


class InProcessRegexExecutor:
    """Matches on the calling thread. `re` cannot be interrupted mid-match, so a result that lands
    after the deadline is reported as `Timeout`, but a catastrophic pattern still blocks the caller:
    never serve untrusted patterns with it."""

    def __init__(self, caps: ExtractCaps = EXTRACT) -> None:
        self._caps = caps

    async def find(self, pattern: Translated, text: str, *, deadline: float) -> MatchResult:
        if monotonic() >= deadline:
            return Timeout()
        try:
            matches = match_all(pattern, text, self._caps.candidates_per_field, self._caps.candidate_units)
        except re.error:
            return Invalid(Unavailable.NO_RESULT)
        return Timeout() if monotonic() > deadline else matches

    async def aclose(self) -> None:
        return None
