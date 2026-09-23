"""Per-run outcome measures read from the agent's trace and the Jev proxy log. Pure: no process, no I/O.

Definitions the report states, in one place:

- **Reached the model:** at least one frontier call produced output tokens. A run that never did (a
  connection error, a server that was down) is a harness failure, not a fast or slow completion.
- **Jev tool called:** the proxy in front of the Jev server logged at least one call to a Jev tool. The
  proxy sits between the agent's MCP client and the server, so a row exists only if the agent called.
- **Jev used:** at least one of those calls was answered without error by the pinned model
  (`evals.bench.gate.use_gate`). An arm-B run without it is a failed measurement.
- **Test cycle:** a shell tool call whose command runs `unittest` or `pytest`. **Retries** are the test
  cycles after the first; whether a cycle failed is not read, because the agents' tool-error flags are
  not verified to mirror a test run's exit status.
- **Wrong branch:** a non-gold option whose declared signature (`task.json`) appears in what the agent
  wrote (every string in a write or edit tool's input, except the text being replaced) or in a shell
  command. A signature match is a heuristic lower bound; a task that declares none reports no count.
- **Decision:** the option id on the agent's last `{"decision": ...}` JSON line, if it names an option.
"""

import json
import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Any, cast

from evals.ab.stream import ToolUse, Trace
from evals.ab.tasks import Judgment
from evals.bench.gate import JEV_TOOLS, use_gate

SHELL_TOOLS = frozenset({"bash"})
WRITE_TOOLS = frozenset({"edit", "write", "multiedit", "notebookedit"})
TEST_RUN = re.compile(r"\b(unittest|pytest)\b")
_REPLACED_KEY = re.compile(r"^old", re.IGNORECASE)
"""Input keys holding the text an edit replaces (`old_string`, `oldText`): not something the agent wrote."""


def reached_model(trace: Trace) -> bool:
    return trace.frontier_calls > 0 and trace.output_tokens > 0


def jev_rows(calls: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    return [call for call in calls if call.get("tool") in JEV_TOOLS]


def jev_gate(calls: Sequence[Mapping[str, Any]]) -> str | None:
    """None when the run got a Jev answer from the pinned model, else why it did not."""
    return use_gate(calls)


def _strings(value: object, *, skip_replaced: bool) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, Mapping):
        for key, item in cast(Mapping[str, object], value).items():
            if not (skip_replaced and _REPLACED_KEY.match(str(key))):
                yield from _strings(item, skip_replaced=skip_replaced)
    elif isinstance(value, list):
        for item in cast(list[object], value):
            yield from _strings(item, skip_replaced=skip_replaced)


def _kind(use: ToolUse) -> str | None:
    name = use.name.lower()
    if name in SHELL_TOOLS:
        return "shell"
    if name in WRITE_TOOLS:
        return "write"
    return None


def written(uses: Sequence[ToolUse]) -> list[str]:
    """Every string the agent wrote or ran: write-tool inputs (minus replaced text) and shell commands."""
    out: list[str] = []
    for use in uses:
        kind = _kind(use)
        if kind is not None:
            out.extend(_strings(dict(use.input), skip_replaced=kind == "write"))
    return out


def test_cycles(uses: Sequence[ToolUse]) -> int:
    return sum(
        1
        for use in uses
        if _kind(use) == "shell" and any(TEST_RUN.search(s) for s in _strings(dict(use.input), skip_replaced=False))
    )


def wrong_branches(uses: Sequence[ToolUse], judgment: Judgment) -> list[str] | None:
    """The non-gold options the agent committed to at some point, or None when the task declares no
    signatures (or has no gold), so no count exists."""
    if judgment.gold is None or not judgment.wrong_branch_signatures:
        return None
    text = "\n".join(written(uses))
    return sorted(
        option
        for option, patterns in judgment.wrong_branch_signatures.items()
        if option != judgment.gold and any(re.search(pattern, text) for pattern in patterns)
    )


def decision(result_text: object, judgment: Judgment) -> str | None:
    if not isinstance(result_text, str):
        return None
    for line in reversed(result_text.strip().splitlines()):
        line = line.strip().strip("`")
        if not line.startswith("{"):
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and "decision" in payload:
            chosen = cast(dict[str, object], payload)["decision"]
            return chosen if isinstance(chosen, str) and chosen in judgment.options else None
    return None


def decision_correct(chosen: str | None, judgment: Judgment) -> bool | None:
    """None when the task has no gold: accuracy is then not judged."""
    return None if judgment.gold is None else chosen == judgment.gold


def tokens(trace: Trace) -> dict[str, int]:
    """Context tokens summed over frontier calls, output tokens, and their total."""
    return {
        "context": trace.context_total,
        "output": trace.output_tokens,
        "total": trace.context_total + trace.output_tokens,
    }


def measurement(
    arm: str, *, status: str, reached: bool, calls: Sequence[Mapping[str, Any]], mcp_servers: Mapping[str, str]
) -> str | None:
    """None when the run is a measurement, else why it is not. A run that reached the model and then
    failed (timeout, wrong fix) is a measured failure; only harness faults and an unused Jev drop it."""
    if status.startswith("failed: post-processing raised"):
        return "harness error"
    if not reached:
        return "never reached the model"
    if arm == "A":
        return "Jev tool called in the without-Jev arm" if jev_rows(calls) else None
    if mcp_servers and mcp_servers.get("jev") != "connected":
        return f"jev server {mcp_servers.get('jev')}"
    gate = jev_gate(calls)
    return None if gate is None else f"Jev not used: {gate}"
