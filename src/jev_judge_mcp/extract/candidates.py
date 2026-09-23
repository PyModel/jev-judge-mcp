"""One field's candidate pipeline: caller pattern and flags in, verbatim candidates or a refusal out.

The whole path the reference runs per field (`index.ts:884-903`), for production and the Node
differential test alike: normalize the flags, translate the dialect (ADR-0004), take the absolute
deadline before admission (ADR-0016), run the executor, map candidates back from unit space, and
classify every refusal into its caller-facing reason. Executors return plain results
(`executor.MatchResult`); the frozen reason text lives only here.
"""

from dataclasses import dataclass
from time import monotonic
from typing import Final, Literal

from jev_judge_mcp.extract.dialect import PatternRejected, from_units, translate
from jev_judge_mcp.extract.executor import Invalid, Matches, RegexExecutor, Saturated, Timeout, Unavailable

REGEX_TIMEOUT_S: Final = 1.0
"""`REGEX_TIMEOUT_MS` (`lib.ts:185`)."""

REGEX_TIMEOUT_REASON: Final = "regex timed out after 1000ms; simplify the pattern"

REGEX_POOL_SATURATED_REASON: Final = "regex_pool_saturated"
"""ADR-0025: a queue-bound refusal is a capacity signal, not a pattern problem — no timeout
figure, no advice about the pattern, a stable token an agent (or P9) can tell apart."""

_UNAVAILABLE_REASONS: Final = {
    Unavailable.NO_RESULT: "regex worker exited without a result",
    Unavailable.NOT_STARTED: "regex worker did not start",
}


@dataclass(frozen=True, slots=True)
class Found:
    """The field's candidates, verbatim document text in match order."""

    candidates: list[str]
    truncated: bool
    too_long: int


@dataclass(frozen=True, slots=True)
class Refused:
    """No candidates: `reason` is the caller-facing text, `outcome` the telemetry label."""

    outcome: Literal["rejected", "saturated", "timeout", "worker_error"]
    reason: str


def normalize_flags(flags: str) -> str:
    """`((flags ?? "").replace(/[^a-z]/g, "") + "g").replace(/g+/g, "g")` (`index.ts:992`)."""
    letters = "".join(char for char in flags if "a" <= char <= "z") + "g"
    out: list[str] = []
    for char in letters:
        if not (char == "g" and out and out[-1] == "g"):
            out.append(char)
    return "".join(out)


async def find_candidates(executor: RegexExecutor, pattern: str, flags: str, units: str) -> Found | Refused:
    """Run caller `pattern` with caller `flags` over unit-space `units` (`dialect.to_units`)."""
    try:
        translated = translate(pattern, normalize_flags(flags))
    except PatternRejected as rejected:
        return Refused("rejected", str(rejected))
    result = await executor.find(translated, units, deadline=monotonic() + REGEX_TIMEOUT_S)
    match result:
        case Matches(candidates, truncated, too_long):
            return Found([from_units(candidate) for candidate in candidates], truncated, too_long)
        case Saturated():
            # Its own reason: the refusal spends no time, so it must not borrow the timeout's voice.
            return Refused("saturated", REGEX_POOL_SATURATED_REASON)
        case Timeout():
            return Refused("timeout", REGEX_TIMEOUT_REASON)
        case Invalid(cause):
            return Refused("worker_error", _UNAVAILABLE_REASONS[cause])
