"""In-process spans and metrics (ROADMAP P9). Nothing here writes to stdout or the wire.

A span carries its name, a duration, and attributes that are counts, flags, or enumerated labels —
never evidence, claims, diffs, candidate text, or keys. Payload text is recorded only when the
`JEV_MCP_TELEMETRY_PAYLOADS` debug flag is on, and only into the span's separate `payloads`.

Every finished span goes to two sinks: `SpanLog` keeps recent spans and logs each one at DEBUG
(stderr, through the redacting handler), and `Metrics` folds it into counters and duration
histograms. The span tree follows the call: `mcp.tool` is the root, and `jev.evaluate`,
`jev.validate`, and `regex.extract` open under it through a context variable, so the
pure layers stay free of any recorder.
"""

import logging
import math
from bisect import bisect_left
from collections import deque
from collections.abc import Callable
from contextvars import ContextVar
from dataclasses import dataclass, field
from time import perf_counter
from types import TracebackType
from typing import Final, Literal, Protocol

type SpanName = Literal["mcp.tool", "jev.evaluate", "jev.validate", "regex.extract"]
type Attribute = bool | int | float | str

logger = logging.getLogger("jev_judge_mcp.telemetry")


@dataclass(slots=True, eq=False)
class Span:
    """One timed step. `duration` is in seconds; `payloads` stays empty unless the debug flag is on."""

    name: SpanName
    attributes: dict[str, Attribute]
    parent: "Span | None" = None
    duration: float = 0.0
    payloads: dict[str, str] = field(default_factory=dict[str, str])


class SpanSink(Protocol):
    def record(self, span: Span) -> None: ...


class SpanLog:
    """The most recent finished spans, oldest first. Each is also logged at DEBUG."""

    def __init__(self, capacity: int = 1024) -> None:
        self.spans: deque[Span] = deque(maxlen=capacity)

    def record(self, span: Span) -> None:
        self.spans.append(span)
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug("span %s", describe(span))


def describe(span: Span) -> str:
    """`name duration_ms k=v ...`, payloads last. The log handler redacts configured secrets."""
    parts = [span.name, f"{span.duration * 1000:.3f}ms"]
    parts += [f"{key}={value}" for key, value in span.attributes.items()]
    parts += [f"{key}={value!r}" for key, value in span.payloads.items()]
    return " ".join(parts)


DURATION_BUCKETS_MS: Final = (0.1, 0.5, 1.0, 2.5, 5.0, 10.0, 20.0, 50.0, 100.0, 250.0, 1000.0, 5000.0, math.inf)
ACTIONS: Final = ("auto", "review", "escalate")
CAP_SCOPES: Final = ("context", "item")
METRICS_INTERVAL_SPANS: Final = 100
"""Finished `mcp.tool` spans between periodic INFO metrics snapshots (ROADMAP P9)."""


class Metrics:
    """Counters and per-span duration histograms, keyed Prometheus-style: `name{label="value"}`.

    - `calls{tool}` and `outcomes{tool,outcome}` per `mcp.tool`. `actions{action}` counts each
      call's one headline auto/review/escalate Action (a call with none adds nothing);
      `item_actions{tool,action}` counts the per-item Actions of verify, classify and extract.
      `truncated{scope}` counts calls that cut a document the judgment is made over (`context`)
      or a candidate's or class's own text (`item`, telemetry only), per `validation/caps.py`.
    - `provider_errors{error}` and `tokens{direction}` per `jev.evaluate`.
    - `fail_closed{kind}` counts answers validation rejected.
    - `regex_timeouts` counts patterns that ran out of time.
    - `duration_ms_bucket{span,le}` (cumulative), `duration_ms_sum{span}`, `duration_ms_count{span}`.
    """

    def __init__(self) -> None:
        self._values: dict[str, float] = {}
        self._durations: dict[SpanName, _Histogram] = {}

    def record(self, span: Span) -> None:
        attributes = span.attributes
        match span.name:
            case "mcp.tool":
                self._add(f'calls{{tool="{attributes["tool"]}"}}')
                self._add(f'outcomes{{tool="{attributes["tool"]}",outcome="{attributes.get("outcome", "none")}"}}')
                headline = attributes.get("action")
                for action in ACTIONS:
                    self._add(f'actions{{action="{action}"}}', int(headline == action))
                    items = _number(attributes.get(f"item_actions.{action}", 0))
                    if items:
                        self._add(f'item_actions{{tool="{attributes["tool"]}",action="{action}"}}', items)
                for scope in CAP_SCOPES:
                    if attributes.get(f"truncated.{scope}") is True:
                        self._add(f'truncated{{scope="{scope}"}}')
            case "jev.evaluate":
                if "error" in attributes:
                    self._add(f'provider_errors{{error="{attributes["error"]}"}}')
                self._add('tokens{direction="input"}', _number(attributes.get("input_tokens", 0)))
                self._add('tokens{direction="output"}', _number(attributes.get("output_tokens", 0)))
            case "jev.validate":
                if attributes.get("valid") is False:
                    self._add(f'fail_closed{{kind="{attributes["kind"]}"}}')
            case "regex.extract":
                if attributes.get("outcome") == "timeout":
                    self._add("regex_timeouts")
        self._observe(span.name, span.duration * 1000)

    def _add(self, key: str, amount: float = 1) -> None:
        self._values[key] = self._values.get(key, 0) + amount

    def _observe(self, name: SpanName, ms: float) -> None:
        histogram = self._durations.get(name)
        if histogram is None:
            histogram = self._durations[name] = _Histogram()
        histogram.observe(ms)

    def snapshot(self) -> dict[str, float]:
        values = dict(self._values)
        for name, histogram in self._durations.items():
            cumulative = 0
            for bound, count in zip(DURATION_BUCKETS_MS, histogram.counts, strict=True):
                cumulative += count
                if cumulative:
                    le = "+Inf" if bound == math.inf else f"{bound:g}"
                    values[f'duration_ms_bucket{{span="{name}",le="{le}"}}'] = cumulative
            values[f'duration_ms_sum{{span="{name}"}}'] = histogram.total
            values[f'duration_ms_count{{span="{name}"}}'] = histogram.count
        return dict(sorted(values.items()))


class _Histogram:
    """Per-bucket counts, cumulated only at snapshot time: observing is one bisect and three adds."""

    __slots__ = ("count", "counts", "total")

    def __init__(self) -> None:
        self.counts = [0] * len(DURATION_BUCKETS_MS)
        self.total = 0.0
        self.count = 0

    def observe(self, ms: float) -> None:
        self.counts[bisect_left(DURATION_BUCKETS_MS, ms)] += 1
        self.total += ms
        self.count += 1


def _number(value: Attribute) -> float:
    return value if isinstance(value, int | float) and not isinstance(value, bool) else 0


_active: ContextVar[tuple["Telemetry", Span] | None] = ContextVar("jev_judge_mcp_telemetry_span", default=None)


class Telemetry:
    """A server's recorder: opens spans and hands each finished one to the span log and metrics."""

    def __init__(
        self, *, payloads: bool = False, capacity: int = 1024, metrics_interval: int = METRICS_INTERVAL_SPANS
    ) -> None:
        self.payloads = payloads
        """The debug flag: whether spans may record payload text."""
        self.metrics_interval = metrics_interval
        """Finished `mcp.tool` spans between INFO metrics snapshots; 0 disables the periodic log."""
        self.spans = SpanLog(capacity)
        self.metrics = Metrics()
        self._sinks: tuple[SpanSink, ...] = (self.spans, self.metrics)
        self._since_metrics = 0

    def span(self, name: SpanName, **attributes: Attribute) -> "_Scope":
        """Time a `with` block as a child of the active span. An escaping exception is named, never quoted."""
        return _Scope(self, name, attributes)

    def record(self, span: Span) -> None:
        for sink in self._sinks:
            sink.record(span)
        if span.name == "mcp.tool" and self.metrics_interval > 0:
            self._since_metrics += 1
            if self._since_metrics >= self.metrics_interval:
                self._since_metrics = 0
                logger.info("metrics %s", self.metrics.snapshot())

    def payload(self, span: Span, key: str, text: Callable[[], str]) -> None:
        """Record payload text on `span` only under the debug flag; `text` is not called otherwise."""
        if self.payloads:
            span.payloads[key] = text()


class _Scope:
    """The `with` block behind a span; a class rather than a generator, as spans sit on the hot path."""

    __slots__ = ("_start", "_telemetry", "_token", "span")

    def __init__(self, telemetry: Telemetry | None, name: SpanName, attributes: dict[str, Attribute]) -> None:
        self._telemetry = telemetry
        self.span = Span(name, attributes)

    def __enter__(self) -> Span:
        if self._telemetry is not None:
            active = _active.get()
            self.span.parent = None if active is None else active[1]
            self._token = _active.set((self._telemetry, self.span))
            self._start = perf_counter()
        return self.span

    def __exit__(self, kind: type[BaseException] | None, error: BaseException | None, _: TracebackType | None) -> None:
        if self._telemetry is None:
            return
        span = self.span
        span.duration = perf_counter() - self._start
        _active.reset(self._token)
        if kind is not None:
            if issubclass(kind, Exception):
                span.attributes["error"] = kind.__name__
            else:
                span.attributes["cancelled"] = True
        self._telemetry.record(span)


def span(name: SpanName, **attributes: Attribute) -> _Scope:
    """A child of the active span, recorded by its telemetry. Outside any span, nothing is recorded."""
    active = _active.get()
    return _Scope(None if active is None else active[0], name, attributes)
