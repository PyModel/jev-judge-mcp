"""Server spans to per-call timings (S2-S5), by order only, never guessed.

The Jev server logs each finished span to stderr at DEBUG (`jev_judge_mcp.telemetry: span <name> <ms>ms
k=v ...`). Spans carry no call id, and a child finishes before its parent, so a call is the run of
`jev.evaluate` spans closed by one `mcp.tool` span. The k-th such group is the k-th forwarded call
only when a run's calls never overlap, the counts agree, and each group's `tool` matches; otherwise
the run's calls are unattributed (`None`). A call with no `jev.evaluate` made no provider request
(extract with zero candidates): its provider time is `None`, not 0.
"""

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from itertools import pairwise
from typing import Any

SPAN_LINE = re.compile(r"jev_judge_mcp\.telemetry: span (\S+) ([0-9.]+)ms(.*)$")


@dataclass(frozen=True, slots=True)
class Span:
    name: str
    ms: float
    attributes: dict[str, str]


@dataclass(frozen=True, slots=True)
class CallTiming:
    round_trip_ms: float
    """S2: the proxy's request-to-response time."""
    provider_ms: float | None
    """S3: the `jev.evaluate` span; None when the call made no provider request."""
    server_ms: float
    """S4: `mcp.tool` minus `jev.evaluate`."""
    relay_ms: float
    """S5: S2 minus `mcp.tool`."""


def parse_spans(lines: Iterable[str]) -> list[Span]:
    spans: list[Span] = []
    for line in lines:
        match = SPAN_LINE.search(line.rstrip("\n"))
        if match:
            pairs = (part.partition("=") for part in match.group(3).split())
            spans.append(Span(match.group(1), float(match.group(2)), {k: v for k, _, v in pairs}))
    return spans


def _groups(spans: Sequence[Span]) -> list[tuple[Span, list[Span]]] | None:
    groups: list[tuple[Span, list[Span]]] = []
    evaluations: list[Span] = []
    for span in spans:
        if span.name == "jev.evaluate":
            evaluations.append(span)
        elif span.name == "mcp.tool":
            groups.append((span, evaluations))
            evaluations = []
    return None if evaluations else groups


def _overlap(calls: Sequence[Mapping[str, Any]]) -> bool:
    ordered = sorted(calls, key=lambda call: float(call["t0"]))
    return any(float(later["t0"]) < float(earlier["t1"]) for earlier, later in pairwise(ordered))


def attribute(calls: Sequence[Mapping[str, Any]], spans: Sequence[Span]) -> list[CallTiming] | None:
    """Timings for the forwarded calls in arrival order, or None when they cannot be attributed."""
    forwarded = sorted((call for call in calls if not call.get("refused")), key=lambda call: int(call["seq"]))
    groups = _groups(spans)
    if groups is None or len(groups) != len(forwarded) or _overlap(forwarded):
        return None
    timings: list[CallTiming] = []
    for call, (tool, evaluations) in zip(forwarded, groups, strict=True):
        if tool.attributes.get("tool") != call.get("tool") or len(evaluations) > 1:
            return None
        provider = evaluations[0].ms if evaluations else None
        round_trip = float(call["ms"])
        server, relay = round(tool.ms - (provider or 0.0), 3), round(round_trip - tool.ms, 3)
        timings.append(CallTiming(round_trip, provider, server, relay))
    return timings
