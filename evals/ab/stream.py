"""Parse `claude --print --output-format stream-json --verbose` output into per-run measures.

Each assistant API message appears once per content block, all blocks sharing one message id and one
usage record, so a frontier call is a distinct message id. Context per call is the prompt the model
saw: uncached input plus cache reads plus cache writes. Output tokens are the result's `usage`, else the
largest count each message reported, summed over messages. Each `tool_result` block in a user event is
keyed by the `tool_use` id it answers, with its `is_error` flag.
"""

import json
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, cast


@dataclass(frozen=True)
class ToolUse:
    name: str
    input: Mapping[str, object]
    id: str = ""


@dataclass
class Trace:
    model: str | None = None
    tools: tuple[str, ...] = ()
    mcp_servers: dict[str, str] = field(default_factory=dict[str, str])
    frontier_calls: int = 0
    context_peak: int = 0
    context_total: int = 0
    output_tokens: int = 0
    tool_uses: list[ToolUse] = field(default_factory=list[ToolUse])
    tool_results: dict[str, bool] = field(default_factory=dict[str, bool])
    """`is_error` per answered `tool_use` id."""
    result: dict[str, Any] | None = None
    torn_tail: bool = False
    """The final event line was cut mid-JSON (a timeout's partial stdout); it ended the stream."""

    @property
    def tool_counts(self) -> Counter[str]:
        return Counter(use.name for use in self.tool_uses)

    def result_field(self, key: str) -> Any:
        return None if self.result is None else self.result.get(key)


def is_torn_tail(events: Sequence[str], index: int) -> bool:
    """A `JSONDecodeError` at `index` is a cut tail iff no later line looks like an event.

    A timeout stops the agent mid-write, so the last stdout line can end mid-JSON: that line ends
    the stream. Garbage with a later event behind it is a structural break and still raises.
    """
    return not any(later.startswith("{") for later in events[index + 1 :])


def parse(lines: Iterable[str]) -> Trace:
    trace = Trace()
    contexts: dict[str, int] = {}
    outputs: dict[str, int] = {}
    events = [line.strip() for line in lines]
    for index, line in enumerate(events):
        if not line.startswith("{"):
            continue
        try:
            event = cast(dict[str, Any], json.loads(line))
        except json.JSONDecodeError:
            if is_torn_tail(events, index):
                trace.torn_tail = True
                break
            raise
        kind = event.get("type")
        if kind == "system" and event.get("subtype") == "init":
            trace.model = event.get("model")
            trace.tools = tuple(event.get("tools") or ())
            servers = cast(list[dict[str, Any]], event.get("mcp_servers") or [])
            trace.mcp_servers = {str(s.get("name")): str(s.get("status")) for s in servers}
        elif kind == "assistant":
            message = cast(dict[str, Any], event.get("message") or {})
            usage = cast(dict[str, Any], message.get("usage") or {})
            message_id = str(message.get("id"))
            outputs[message_id] = max(outputs.get(message_id, 0), int(usage.get("output_tokens") or 0))
            contexts[message_id] = sum(
                int(usage.get(key) or 0)
                for key in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")
            )
            for block in cast(list[dict[str, Any]], message.get("content") or []):
                if block.get("type") == "tool_use":
                    name, arguments = str(block.get("name")), cast(dict[str, object], block.get("input"))
                    trace.tool_uses.append(ToolUse(name, arguments, str(block.get("id") or "")))
        elif kind == "user":
            content = cast(dict[str, Any], event.get("message") or {}).get("content")
            blocks = cast(list[object], content) if isinstance(content, list) else []
            for block in (cast(dict[str, Any], b) for b in blocks if isinstance(b, dict)):
                if block.get("type") == "tool_result":
                    trace.tool_results[str(block.get("tool_use_id"))] = bool(block.get("is_error"))
        elif kind == "result":
            trace.result = event
    trace.frontier_calls = len(contexts)
    trace.context_peak = max(contexts.values(), default=0)
    trace.context_total = sum(contexts.values())
    reported = cast(dict[str, Any], trace.result_field("usage") or {}).get("output_tokens")
    trace.output_tokens = int(reported) if reported is not None else sum(outputs.values())
    return trace
