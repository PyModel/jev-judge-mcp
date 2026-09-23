"""The use gate and the proxy-vs-stream cross-check for a B run.

A B run used Jev only if the proxy log holds at least one call to one of the ten Jev tools that the
server answered (`refused` false) without error and with the pinned model. Anything else fails the
run: no call, only errored or refused calls, or no answer from the pinned model. A successful answer
from any other model also invalidates the study, as `evals.runners.live` aborts on a model mismatch.
"""

from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any

from evals.ab.arms import JEV_MODEL
from evals.ab.stream import Trace
from evals.scorers.tools import SCORERS

JEV_TOOLS = tuple(SCORERS)
"""The server's tool names, registry order; Claude Code calls them `mcp__jev__<name>`."""
STREAM_PREFIX = "mcp__jev__"

Call = Mapping[str, Any]


class HarnessMismatchError(RuntimeError):
    """The proxy log and the agent's stream disagree about the Jev calls: a harness bug, not a result."""


class WrongModelError(RuntimeError):
    """A Jev answer came from a model other than the pin: the snapshot is broken and the study stops."""


def _answered(call: Call) -> bool:
    return call.get("tool") in JEV_TOOLS and not call.get("refused") and not call.get("is_error")


def use_gate(calls: Sequence[Call]) -> str | None:
    """None when the run got a Jev answer from the pinned model, else why it failed the gate."""
    jev = [call for call in calls if call.get("tool") in JEV_TOOLS]
    if not jev:
        return "no Jev call"
    answered = [call for call in jev if _answered(call)]
    if not answered:
        return "no successful Jev call (only errored or refused calls)"
    if not any(call.get("model") == JEV_MODEL for call in answered):
        return f"no Jev answer from {JEV_MODEL}"
    return None


def automatic_note(calls: Sequence[Call]) -> str | None:
    """What an automatic (arm B) run did with Jev. None means it got a pinned answer.

    Not calling is data, reported as "did not call Jev", and is not a failure. A call that never
    produced an answer is data too. The forced arm uses `use_gate` instead.
    """
    reason = use_gate(calls)
    if reason is None:
        return None
    if reason == "no Jev call":
        return "did not call Jev"
    if reason.startswith("no successful"):
        return "called Jev, no answer"
    return reason


def wrong_models(calls: Sequence[Call]) -> list[str]:
    return sorted({str(call.get("model")) for call in calls if _answered(call) and call.get("model") != JEV_MODEL})


def cross_check(calls: Sequence[Call], trace: Trace) -> None:
    """The stream's `mcp__jev__` tool uses and the proxy rows must be the same calls with the same error flags.

    Compared as multisets: parallel calls can reach the proxy in a different order than the stream lists them.
    A call the server never answered (`unanswered`, logged errored at the relay's EOF) matches a use the
    stream reports as an error or never got a result for: the run was cut short, the harness is sound.
    """
    uses = [use for use in trace.tool_uses if use.name.startswith(STREAM_PREFIX)]
    streamed = [use.name.removeprefix(STREAM_PREFIX) for use in uses]
    logged = [str(call.get("tool")) for call in sorted(calls, key=lambda call: int(call["seq"]))]
    if sorted(streamed) != sorted(logged):
        raise HarnessMismatchError(f"stream Jev calls {streamed} != proxy calls {logged}")
    stream_flags: Counter[tuple[str, bool | None]] = Counter(
        (name, trace.tool_results.get(use.id)) for name, use in zip(streamed, uses, strict=True)
    )
    answered: Counter[tuple[str, bool | None]] = Counter(
        (str(call.get("tool")), bool(call.get("is_error"))) for call in calls if not call.get("unanswered")
    )
    unanswered = Counter(str(call.get("tool")) for call in calls if call.get("unanswered"))
    rest = stream_flags - answered
    if (
        answered - stream_flags
        or any(flag is False for _, flag in rest)
        or Counter(n for n, _ in rest.elements()) != unanswered
    ):
        stream_errors = sorted((name, repr(flag)) for name, flag in stream_flags.elements())
        proxy_errors = sorted(
            (str(call.get("tool")), "unanswered" if call.get("unanswered") else repr(bool(call.get("is_error"))))
            for call in calls
        )
        raise HarnessMismatchError(f"stream Jev errors {stream_errors} != proxy errors {proxy_errors}")
