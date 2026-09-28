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


_MCPSCRIPT_CALL = re.compile(r'tools\.call\(\s*["\'](jev_[a-z]+)["\']')


def exposed_jev_tool(use: ToolUse) -> str | None:
    """The Jev tool a stream use invoked, or None for gateway chatter.

    Pi also reaches the server through `mcpScript`, whose code calls `tools.call("jev_find", ...)`;
    a recorded run used exactly that, so the code is searched for the tool name."""
    name = use.name
    exposed: object = None
    if name in JEV_TOOLS:
        return name
    if name in ("mcp", "mcp__jev"):
        exposed = use.input.get("tool")
    elif name.startswith("mcp__jev__"):
        exposed = name.removeprefix("mcp__jev__")
    elif name == "mcpScript":
        code = use.input.get("code")
        if isinstance(code, str) and (match := _MCPSCRIPT_CALL.search(code)):
            return match.group(1)
        return None
    if not isinstance(exposed, str):
        return None
    if exposed in JEV_TOOLS:
        return exposed
    stripped = exposed.removeprefix("jev_")
    return stripped if stripped in JEV_TOOLS else None


def _question_args(use: ToolUse) -> object:
    """Arguments that identify the question, without the gateway wrapper."""
    if use.name not in ("mcp", "mcp__jev"):
        return dict(use.input)
    inner = use.input.get("args")
    if inner is None:
        inner = use.input.get("arguments")
    if isinstance(inner, str):
        try:
            return json.loads(inner)
        except json.JSONDecodeError:
            return inner
    return inner if inner is not None else {key: value for key, value in use.input.items() if key != "tool"}


def unnecessary_jev_calls(*, control: bool, uses: Sequence[ToolUse], calls: Sequence[Mapping[str, Any]]) -> int:
    """How many proxy-logged Jev calls were unnecessary.

    A call is unnecessary when the task is a control (the code or tests already determine the
    answer) or when it repeats an earlier call of the same tool with the same arguments. The
    repeat count cannot exceed the proxy log: a stream use the proxy did not see is not a call.
    """
    logged = len(jev_rows(calls))
    if control:
        return logged
    seen: set[str] = set()
    repeats = 0
    for use in uses:
        tool = exposed_jev_tool(use)
        if tool is None:
            continue
        key = tool + "\0" + json.dumps(_question_args(use), sort_keys=True, default=str)
        if key in seen:
            repeats += 1
        else:
            seen.add(key)
    return min(repeats, logged)


def _first(value: object, key: str) -> object:
    """`value[0][key]` for list-of-object shapes (top, results); None for anything else."""
    if isinstance(value, list) and value and isinstance(value[0], dict):
        return cast(dict[str, Any], value[0]).get(key)
    return None


def _holder(value: object, key: str) -> object:
    """`value[key]` for object shapes (recommendation, overall); None for anything else."""
    return cast(dict[str, Any], value).get(key) if isinstance(value, dict) else None


def answer_field(doc: dict[str, Any]) -> object:
    """The one field of a Jev result document that names its answer, by tool."""
    tool = str(doc.get("tool"))
    if tool == "jev_find":
        return None if doc.get("exists") is False else _first(doc.get("top"), "id")
    if tool == "jev_decide":
        return _holder(doc.get("recommendation"), "selected")
    if tool in ("jev_classify", "jev_verify"):
        return _first(doc.get("results"), "verdict" if tool == "jev_verify" else "class")
    if tool == "jev_compare":
        return _holder(doc.get("overall"), "relation")
    if tool == "jev_screen":
        return _holder(doc.get("recommendation"), "action")
    if tool in ("jev_gate", "jev_review"):
        return doc.get("action")
    return None


def _documents(result: object) -> list[dict[str, Any]]:
    """Every JSON object the MCP server returned as text (Pi `result`, Claude `content[].text`)."""
    raw_blocks: object
    if isinstance(result, dict):
        raw_blocks = cast(dict[str, Any], result).get("content")
    else:
        raw_blocks = result
    blocks = cast(list[object], raw_blocks) if isinstance(raw_blocks, list) else [raw_blocks]
    docs: list[dict[str, Any]] = []
    for raw_block in blocks:
        text: object = cast(dict[str, Any], raw_block).get("text") if isinstance(raw_block, dict) else raw_block
        if isinstance(text, str):
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                docs.append(cast(dict[str, Any], parsed))
    return docs


def jev_answer_from_result(result: object, options: Mapping[str, str], verdicts: Mapping[str, str]) -> str | None:
    """The task option Jev's result selects, or None. `verdicts` maps a verdict word to an option id
    for the verdict-word tools (verify, compare, screen, gate, review); find, decide, and classify
    answer with an option id directly. A document that names neither is not an answer."""
    for doc in _documents(result):
        value = answer_field(doc)
        if isinstance(value, str):
            if value in options:
                return value
            if value in verdicts:
                return verdicts[value]
    return None


def jev_answer(stream: str, options: Mapping[str, str], verdicts: Mapping[str, str] | None = None) -> str | None:
    """The last task option a Jev result selected in the agent's stream, or None.

    The proxy does not keep result text, so this reads the stream's tool-result payloads only: the
    agent's own decision line is not Jev's answer. The comparison it feeds is a B-versus-paired-A
    proxy, not within-run causality.
    """
    option_ids = frozenset(options)
    verdict_map = verdicts or {}
    if not option_ids or not stream:
        return None
    last: str | None = None
    for raw in stream.splitlines():
        line = raw.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        payload = cast(dict[str, Any], event)
        kind = payload.get("type")
        if kind == "tool_execution_end":
            if payload.get("isError") is not True:
                match = jev_answer_from_result(payload.get("result"), options, verdict_map)
                if match is not None:
                    last = match
        elif kind == "user":
            message = cast(dict[str, Any], payload.get("message") or {})
            for raw_item in cast(list[object], message.get("content") or []):
                if not isinstance(raw_item, dict):
                    continue
                block = cast(dict[str, Any], raw_item)
                if block.get("type") == "tool_result" and block.get("is_error") is not True:
                    match = jev_answer_from_result(block.get("content"), options, verdict_map)
                    if match is not None:
                        last = match
    return last


def failure_category(
    *,
    status: str,
    success: bool,
    acceptance_passed: int,
    acceptance_total: int,
    regressions: Sequence[str],
    preexisting_altered: Sequence[str],
    added_failing: bool,
    added_irrelevant: bool = False,
    decision_matches_gold: bool | None = None,
    jev_calls: Sequence[Mapping[str, Any]] = (),
) -> str | None:
    """Why a failed run failed. First match wins. A correct run has no category.

    Order: timeout, agent error, acceptance miss, regression, pre-existing test altered,
    added test failing, added test irrelevant, no decision, wrong decision. The unreachable tail
    the critique found is gone: `run_correct` already folded Jev errors into acceptance failures
    and harness errors into the escape/harness paths, so no category claims a cause the record
    path never produces.
    """
    if success:
        return None
    lowered = status.lower()
    if "timeout" in lowered:
        return "timeout"
    if status.startswith("failed:") and "post-processing" not in lowered:
        return "agent error"
    if acceptance_passed < acceptance_total:
        return "acceptance miss"
    if regressions:
        return "regression"
    if preexisting_altered:
        return "pre-existing test altered"
    if added_failing:
        return "added test failing"
    if added_irrelevant:
        return "irrelevant added test"
    if decision_matches_gold is None:
        return "no decision"
    if decision_matches_gold is False:
        return "wrong decision"


def tokens(trace: Trace) -> dict[str, int]:
    """Context tokens summed over frontier calls, output tokens, and their total."""
    return {
        "context": trace.context_total,
        "output": trace.output_tokens,
        "total": trace.context_total + trace.output_tokens,
    }


def measurement(
    arm: str,
    *,
    status: str,
    reached: bool,
    calls: Sequence[Mapping[str, Any]],
    mcp_servers: Mapping[str, str],
    control: bool = False,
) -> str | None:
    """None when the run is a measurement, else why it is not. A run that reached the model and then
    failed (timeout, wrong fix) is a measured failure; only harness faults and an unused Jev drop it.

    A control task's with-Jev run that never called Jev is MEASURED: restraint on a task whose
    answer the code already settles is the outcome the control exists to observe, not a void."""
    if status.startswith("failed: post-processing raised"):
        return "harness error"
    if not reached:
        return "never reached the model"
    if arm == "A":
        return "Jev tool called in the without-Jev arm" if jev_rows(calls) else None
    if mcp_servers and mcp_servers.get("jev") != "connected":
        return f"jev server {mcp_servers.get('jev')}"
    if control and not jev_rows(calls):
        return None
    gate = jev_gate(calls)
    return None if gate is None else f"Jev not used: {gate}"
