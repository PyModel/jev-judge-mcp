"""The Pi arm's offline pieces: the stream parser, the proxy cross-check, and the unlabeled chart.

No model and no server. The parser and cross-check run on hand-written Pi JSON events; the chart test
checks that a run with no labeled items says accuracy is not measured instead of plotting a bar.
"""

import json
from pathlib import Path
from typing import Any

import pytest

from evals.ab.stream import ToolUse, Trace
from evals.agent import AgentRunResult
from evals.bench import chart, pi, run
from evals.bench.gate import HarnessMismatchError
from evals.bench.items import Item

SUCCESS: list[dict[str, Any]] = [
    {"type": "session", "version": 3, "id": "s"},
    {"type": "agent_start"},
    {"type": "turn_start"},
    {
        "type": "message_end",
        "message": {
            "role": "assistant",
            "provider": "ds4",
            "model": "glm-5.3-flash",
            "usage": {"input": 10, "output": 4, "cacheRead": 0, "cacheWrite": 100, "totalTokens": 114},
            "content": [{"type": "text", "text": '{"answer": "blue"}'}],
            "stopReason": "stop",
        },
    },
    {
        "type": "tool_execution_start",
        "toolCallId": "c1",
        "toolName": "mcp",
        "args": {"tool": "jev_jev_verify", "args": "{}"},
    },
    {"type": "tool_execution_end", "toolCallId": "c1", "toolName": "mcp", "isError": False, "result": {}},
    {"type": "agent_end", "willRetry": False},
    {"type": "agent_settled"},
]


def _lines(events: list[dict[str, Any]]) -> list[str]:
    return [json.dumps(event) for event in events]


def test_parse_reads_pi_events_into_a_trace() -> None:
    trace = pi.parse(_lines(SUCCESS))
    assert trace.model == "ds4/glm-5.3-flash"
    assert trace.result_field("subtype") == "success" and not trace.result_field("is_error")
    assert trace.result_field("result") == '{"answer": "blue"}'
    assert trace.result_field("total_cost_usd") is None and trace.result_field("agent_cost_reported") is False
    assert [(use.name, use.input.get("tool"), use.id) for use in trace.tool_uses] == [("mcp", "jev_jev_verify", "c1")]
    assert trace.tool_results == {"c1": False}
    assert trace.context_total == 110
    assert not trace.torn_tail


def test_a_torn_final_line_ends_the_trace() -> None:
    """A timeout cuts stdout mid-line; the cut line ends the stream instead of aborting the study."""
    trace = pi.parse([*_lines(SUCCESS), '{"type": "message_end", "trunca'])
    assert trace.torn_tail
    assert trace.model == "ds4/glm-5.3-flash"


def test_mid_stream_garbage_still_raises() -> None:
    with pytest.raises(json.JSONDecodeError):
        pi.parse(['{"type": "message_end", "trunca', *_lines(SUCCESS[1:3])])


def test_parse_marks_a_failed_retry_as_an_error() -> None:
    events = [event for event in SUCCESS if event["type"] != "agent_end"]
    events.append({"type": "auto_retry_end", "success": False, "attempt": 3})
    events.append({"type": "agent_end", "willRetry": False})
    trace = pi.parse(_lines(events))
    assert trace.result_field("subtype") == "error" and trace.result_field("is_error")
    assert trace.result_field("server_error") is None


def test_parse_marks_a_connection_error_as_a_server_failure() -> None:
    events: list[dict[str, Any]] = [
        {"type": "agent_start"},
        {"type": "turn_start"},
        {
            "type": "message_end",
            "message": {
                "role": "assistant",
                "provider": "ds4",
                "model": "glm-5.3-flash",
                "usage": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0},
                "content": [],
                "stopReason": "error",
                "errorMessage": "Connection error.",
            },
        },
        {"type": "auto_retry_end", "success": False, "finalError": "Connection error."},
        {"type": "agent_end"},
    ]
    trace = pi.parse(_lines(events))
    assert trace.result_field("server_error") == "Connection error."
    usage = trace.result_field("usage")
    assert isinstance(usage, dict)
    assert usage["reported"] is True and usage["output"] == 0 and usage["cost_reported"] is False


def test_parse_sums_a_reported_usage_cost_and_flags_a_rate_limit() -> None:
    events: list[dict[str, Any]] = [
        {
            "type": "message_end",
            "message": {
                "role": "assistant",
                "provider": "opencode-go",
                "model": "deepseek-v4.1-flash",
                "usage": {"input": 3, "output": 1, "cacheRead": 0, "cacheWrite": 0, "cost": {"total": 0.02}},
                "content": [{"type": "text", "text": "ok"}],
                "stopReason": "stop",
            },
        },
        {"type": "agent_end"},
    ]
    trace = pi.parse(_lines(events))
    assert trace.model == "opencode-go/deepseek-v4.1-flash"
    assert trace.result_field("total_cost_usd") == 0.02 and trace.result_field("agent_cost_reported") is True
    limited = pi.parse(
        _lines([{"type": "auto_retry_end", "success": False, "finalError": "rate limit"}, {"type": "agent_end"}])
    )
    assert limited.result_field("server_error") == "rate limit"


def test_parse_without_an_agent_end_has_no_result() -> None:
    trace = pi.parse(_lines([event for event in SUCCESS if event["type"] != "agent_end"]))
    assert trace.result is None and trace.result_field("subtype") is None


def _proxy(tool: str, *, is_error: bool = False) -> dict[str, object]:
    return {"seq": 0, "tool": tool, "is_error": is_error}


def test_cross_check_maps_the_gateway_prefix_to_the_server_tool() -> None:
    trace = pi.parse(_lines(SUCCESS))
    pi.cross_check([_proxy("jev_verify")], trace)


def test_cross_check_raises_when_the_attributed_calls_disagree() -> None:
    trace = pi.parse(_lines(SUCCESS))
    with pytest.raises(HarnessMismatchError):
        pi.cross_check([_proxy("jev_screen")], trace)


def test_cross_check_allows_a_failed_gateway_attempt_rescued_by_mcp_script() -> None:
    """Both `mcp__jev` tries error client-side; the `mcpScript` forward succeeds and is unattributed."""
    events = [
        event
        for event in SUCCESS
        if not (event.get("type") == "tool_execution_end" and event.get("toolCallId") == "c1")
    ]
    events.append({"type": "tool_execution_end", "toolCallId": "c1", "toolName": "mcp__jev", "isError": True})
    trace = pi.parse(_lines(events))
    pi.cross_check([_proxy("jev_verify")], trace)  # the success is mcpScript's, not the failed gateway try


def test_cross_check_passes_when_nothing_is_attributed() -> None:
    trace = Trace(tool_uses=[ToolUse("mcp", {"server": "jev"}, "c0")])
    pi.cross_check([_proxy("jev_verify")], trace)


def test_unlabeled_chart_says_accuracy_is_not_measured() -> None:
    record: dict[str, Any] = {
        "run_id": "i.A",
        "item": "i",
        "tool": "jev_verify",
        "arm": "A",
        "status": "ok",
        "gate": None,
        "answer": "verified",
        "parse_error": None,
        "correct": False,
        "wall_s": 1.5,
        "jev_calls": [],
    }
    other: dict[str, Any] = {**record, "run_id": "i.B", "arm": "B", "wall_s": 2.5, "jev_calls": [{}]}
    forced: dict[str, Any] = {**record, "run_id": "i.C", "arm": "C", "wall_s": 3.5, "jev_calls": [{}]}
    page = chart.render([record, other, forced], sample=False, stop="all 1 triplets recorded", labeled_items=0)
    assert "not measured (0 labeled items)" in page
    assert "Jev connection: 2 of 2 with-Jev runs that reached the model got a Jev answer (100%)." in page
    assert "A direct" in page and "B automatic" in page and "C forced" in page
    assert "items per minute" in page and "Time consumed: total wall time" in page
    assert page.count("<svg") == 2
    table = chart.numbers_md([record, other, forced], labeled_items=0, stop="all 1 triplets recorded")
    assert "not measured (0 labeled items)" in table and "result.json" in table
    assert "Tokens: not measured." in table


def test_chart_labels_a_grading_failure_apart_from_a_connection_error() -> None:
    def record(item: str, arm: str, status: str) -> dict[str, Any]:
        return {
            "run_id": f"{item}.{arm}",
            "item": item,
            "tool": "jev_verify",
            "arm": arm,
            "status": status,
            "gate": None,
            "answer": "verified",
            "parse_error": None,
            "correct": False,
            "wall_s": 1.5,
            "jev_calls": [],
        }

    # The grading failure comes from the runner's own record builder, so a reworded status fails here.
    item = Item("crash", "jev_verify", "f", {}, "q", None, {}, (), "i", None, None, None, {}, {"status": "draft"})
    agent = AgentRunResult((), Path("."), "", "", 0, 1.5, Trace(), "ok")
    crashed = run.failed_record("crash.B", item, "B", agent, 0.0, KeyError("answer"))
    records = [
        record("ok", "A", "ok"),
        record("ok", "B", "ok"),
        record("ok", "C", "ok"),
        record("down", "A", "ok"),
        record("down", "B", "failed: jev server failed"),
        record("down", "C", "ok"),
        record("crash", "A", "ok"),
        crashed,
        record("crash", "C", "ok"),
    ]
    page = chart.render(records, sample=False, stop="all 3 triplets recorded", labeled_items=0)
    assert "Speed and time use the 1 triplets where every arm reached the model." in page
    assert "1 triplets failed before an answer (local-server connection error)" in page
    assert "1 triplets failed in grading (post-processing raised)" in page
    assert "Jev connection: 4 of 4 with-Jev runs that reached the model got a Jev answer (100%)." in page
    assert "1 with-Jev runs never reached the local model (connection error)" in page
    assert "1 with-Jev runs failed in grading (post-processing raised) and are not a Jev miss." in page
    table = chart.numbers_md(records, labeled_items=0, stop="all 3 triplets recorded")
    assert "1 with-Jev runs never reached the local model (connection error)." in table
    assert "1 with-Jev runs failed in grading (post-processing raised)." in table
    assert (
        "1 triplets failed before an answer (local-server connection error); "
        "1 triplets failed in grading (post-processing raised). Neither is model speed."
    ) in page

    graded_only = [r for r in records if r["item"] != "down"]
    page = chart.render(graded_only, sample=False, stop="all 2 triplets recorded", labeled_items=0)
    assert "local-server connection error" not in page
    assert "1 triplets failed in grading (post-processing raised) and are not model speed." in page
