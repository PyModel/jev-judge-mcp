"""The Pi arm: `pi --print --mode json` against the local ds4 server, parsed into a `Trace`.

Pi's JSON stream (`--mode json`) is not Claude's stream-json: it emits `session`, `message_*`,
`tool_execution_*`, `turn_*`, and `agent_end` events, and never a `system/init` or `result` event, so
`evals.ab.stream.parse` yields an empty trace for it. `parse` reads Pi's events into the same `Trace`
shape, synthesizing the `result` the runner scores from.

Pi has no built-in MCP client. MCP comes from the `pi-mcp-adapter` extension, loaded explicitly with
`--no-extensions -e` so neither arm inherits the user's other extensions or MCP servers. `--mcp-config`
replaces the user's config. Both A/B arms get the harness server. Arm B adds the Jev server,
eager and direct, so the published tools (`jev_verify`, ...) sit in the model's initial tool list.
The `mcp` gateway still exists for search, describe, and status, and can call a Jev tool as
`mcp({tool: "jev_verify"})`.
"""

import json
import os
import re
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any, cast

from evals.ab.stream import ToolUse, Trace, is_torn_tail

PI_MODEL = "ds4/glm-5.3-flash"
PI_THINKING = "high"
_PROVIDER_FAILURE = re.compile(
    r"connection error|stream ended without finish_reason|\b429\b|rate[- ]limit|too many requests|"
    r"\b401\b|unauthorized|invalid api key",
    re.IGNORECASE,
)


class AdapterMissing(RuntimeError):
    """The Pi arm needs `PI_MCP_ADAPTER` naming an existing adapter file."""


def adapter_path() -> Path:
    """The adapter extension, absolute. An unset or dangling `PI_MCP_ADAPTER` fails here, not at import."""
    raw = os.environ.get("PI_MCP_ADAPTER")
    if not raw:
        raise AdapterMissing("PI_MCP_ADAPTER is not set; set it to the pi-mcp-adapter index.ts")
    # The agent runs in its own workdir, so a relative or `~` path must be fixed here.
    path = Path(raw).expanduser().resolve()
    if not path.is_file():
        raise AdapterMissing(f"PI_MCP_ADAPTER is not a file: {path}")
    return path


_CONTEXT_KEYS = ("input", "cacheRead", "cacheWrite")


def pi_command(
    pi: str,
    prompt_text: str,
    mcp_config_path: Path,
    addendum: str,
    model: str = PI_MODEL,
    adapter: str | None = None,
) -> list[str]:
    """The Pi arm's argv. `--` keeps a prompt that starts with `-` from being read as a flag.
    `adapter` names the adapter file; the confined runs pass the container-side copy."""
    return [
        pi,
        "--print",
        "--mode",
        "json",
        "--model",
        model,
        "--thinking",
        PI_THINKING,
        "--no-extensions",
        "-e",
        adapter if adapter is not None else str(adapter_path()),
        "--mcp-config",
        str(mcp_config_path),
        "--no-session",
        "--no-context-files",
        "--no-skills",
        "--no-prompt-templates",
        "--no-themes",
        "--append-system-prompt",
        addendum,
        "--",
        prompt_text,
    ]


def _blocks(message: Mapping[str, Any]) -> list[dict[str, Any]]:
    content = cast(list[object], message.get("content") or [])
    return [cast(dict[str, Any], block) for block in content if isinstance(block, dict)]


def _text(message: Mapping[str, Any]) -> str:
    texts = (str(block.get("text")) for block in _blocks(message) if block.get("type") == "text" and block.get("text"))
    return "\n".join(texts)


def _context(usage: Mapping[str, Any]) -> int:
    return sum(int(usage.get(key) or 0) for key in _CONTEXT_KEYS)


def provider_failure(text: object) -> str | None:
    """The provider error text when it is a connection, auth, or rate-limit failure, else None."""
    if not isinstance(text, str) or _PROVIDER_FAILURE.search(text) is None:
        return None
    return text


def parse(lines: Iterable[str]) -> Trace:
    """Pi's `--mode json` events as a `Trace`.

    The synthesized result is `success` only when the last attempt finished with assistant text and no
    failed auto-retry. A down server makes Pi exit 0, so the result, not the return code, marks the run
    failed.
    """
    trace = Trace()
    contexts: dict[str, int] = {}
    outputs = 0
    turns = 0
    answer = ""
    saw_agent_end = False
    failed_retry = False
    connection_error: str | None = None
    usage_reported = False
    cost_reported = False
    agent_cost = 0.0
    prompt_tokens = cache_read = cache_write = 0
    events = [raw.strip() for raw in lines]
    for index, raw in enumerate(events):
        if not raw.startswith("{"):
            continue
        try:
            event = cast(dict[str, Any], json.loads(raw))
        except json.JSONDecodeError:
            if is_torn_tail(events, index):
                trace.torn_tail = True
                break
            raise
        kind = event.get("type")
        if kind == "message_end":
            message = cast(dict[str, Any], event.get("message") or {})
            role = message.get("role")
            if role == "assistant":
                provider, model = message.get("provider"), message.get("model")
                if isinstance(model, str):
                    trace.model = f"{provider}/{model}" if isinstance(provider, str) else model
                usage = cast(dict[str, Any], message.get("usage") or {})
                if message.get("usage"):
                    usage_reported = True
                    prompt_tokens += int(usage.get("input") or 0)
                    cache_read += int(usage.get("cacheRead") or 0)
                    cache_write += int(usage.get("cacheWrite") or 0)
                    cost = usage.get("cost")
                    if isinstance(cost, dict) and "total" in cost:
                        cost_reported = True
                        priced = cast(dict[str, Any], cost)
                        agent_cost += float(priced.get("total") or 0)
                contexts[str(len(contexts))] = _context(usage)
                outputs += int(usage.get("output") or 0)
                text = _text(message)
                if text:
                    answer = text
            elif role == "system" and message.get("toolsAdded"):
                added = cast(list[object], message.get("toolsAdded") or [])
                names = (str(cast(dict[str, Any], tool).get("name")) for tool in added if isinstance(tool, dict))
                trace.tools = tuple(names)
        elif kind == "tool_execution_start":
            args = event.get("args")
            trace.tool_uses.append(ToolUse(str(event.get("toolName")), _args(args), str(event.get("toolCallId") or "")))
        elif kind == "tool_execution_end":
            trace.tool_results[str(event.get("toolCallId") or "")] = bool(event.get("isError"))
        elif kind == "turn_start":
            turns += 1
        elif kind == "turn_end":
            message = cast(dict[str, Any], event.get("message") or {})
            connection_error = connection_error or provider_failure(message.get("errorMessage"))
        elif kind == "agent_end":
            saw_agent_end = True
        elif kind == "auto_retry_start":
            connection_error = connection_error or provider_failure(event.get("errorMessage"))
        elif kind == "auto_retry_end":
            failed_retry = event.get("success") is False
            if failed_retry:
                connection_error = connection_error or provider_failure(
                    event.get("finalError") or event.get("errorMessage")
                )
    trace.frontier_calls = len(contexts)
    trace.context_peak = max(contexts.values(), default=0)
    trace.context_total = sum(contexts.values())
    trace.output_tokens = outputs
    if saw_agent_end:
        ok = bool(answer) and not failed_retry
        trace.result = {
            "subtype": "success" if ok else "error",
            "is_error": not ok,
            "result": answer,
            "total_cost_usd": agent_cost if cost_reported else None,
            "agent_cost_reported": cost_reported,
            "num_turns": turns,
            "duration_ms": None,
            "usage": {
                "input": prompt_tokens,
                "output": outputs,
                "cache_read": cache_read,
                "cache_write": cache_write,
                "reported": usage_reported,
                "cost_usd": agent_cost if cost_reported else None,
                "cost_reported": cost_reported,
            },
        }
        # A failed retry whose error is a dead server. A retry that then succeeded is a normal run.
        if not ok and failed_retry and connection_error:
            trace.result["server_error"] = connection_error
    return trace


def _args(args: object) -> dict[str, object]:
    return cast(dict[str, object], args) if isinstance(args, dict) else {}
