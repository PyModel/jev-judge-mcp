"""Tools over the real stdio transport: a regex that times out never holds up other calls, the wire
stays protocol-only while worker processes run, and shutdown leaves no worker behind."""

import json
import sys
import time
from typing import Any

import pytest

from jev_judge_mcp.extract.candidates import REGEX_TIMEOUT_REASON, REGEX_TIMEOUT_S
from tests.support.stdio import StdioServer
from tests.support.workers import WORKER_MODULE, alive, worker_pids

SLOW: dict[str, Any] = {
    "document": "a" * 40 + "!",
    "fields": [{"id": "slow", "pattern": "(a|aa)+$", "description": "Pathological."}],
}
NO_MATCH: dict[str, Any] = {
    "document": "nothing here",
    "fields": [{"id": "build", "pattern": "[A-Z]{3}-\\d+", "description": "The build id."}],
}


def call(request_id: int, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    }


def test_worker_census_ignores_a_decoy_command_line() -> None:
    decoy_cmd = [sys.executable, "-c", "input()", "/decoy/python", "-P", "-m", WORKER_MODULE]
    with StdioServer(decoy_cmd) as decoy, StdioServer() as server:
        server.initialize()
        assert worker_pids(server.process.pid)
        assert decoy.process.poll() is None
        assert worker_pids(decoy.process.pid) == set()
        server.close_stdin()
        returncode, _ = server.wait()
        assert returncode == 0


def test_worker_census_retains_a_worker_after_its_launcher_exits() -> None:
    # Keep the worker's stdin open from the test while its immediate parent exits.
    launcher = """
import json, subprocess, sys
worker = subprocess.Popen([sys.executable, "-P", "-m", sys.argv[1]], stdout=subprocess.DEVNULL)
print(json.dumps({"pid": worker.pid}), flush=True)
"""
    with StdioServer([sys.executable, "-c", launcher, WORKER_MODULE]) as server:
        _, frame = server.receive_until(time.monotonic() + 5)
        assert server.process.wait(timeout=5) == 0
        try:
            assert worker_pids(server.process.pid) == {frame["pid"]}
        finally:
            server.close_stdin()


def test_timeout_does_not_block_other_calls_and_shutdown_reaps_workers() -> None:
    with StdioServer() as server:
        server.initialize()
        # Initialization waits for the warm pool (ADR-0058). Snapshot its PIDs before the
        # timed requests, and check the group again after shutdown to cover replacements.
        workers_seen = worker_pids(server.process.pid)
        assert workers_seen, f"server {server.process.pid} has no warm {WORKER_MODULE} workers"
        started = time.monotonic()
        server.send(call(10, "jev_extract", SLOW))
        server.send(call(11, "jev_extract", NO_MATCH))
        # Worker-free probe: `ping` needs no regex worker, so under concurrent dispatch it
        # answers in milliseconds while the slow call cannot reply before its 1 s deadline.
        server.send({"jsonrpc": "2.0", "id": 12, "method": "ping"})
        replies: dict[int, tuple[float, dict[str, Any]]] = {}
        while not {10, 11, 12} <= replies.keys():
            arrived, reply = server.receive_until(started + 10)
            if "id" in reply:
                replies[reply["id"]] = (arrived - started, reply)
        server.close_stdin()
        returncode, stderr = server.wait()
        lines = server.stdout_lines

    assert set(replies) == {10, 11, 12}
    _, quick = replies[11]
    slow_at, slow = replies[10]
    ping_at = replies[12][0]
    # The worker-free ping can answer during the slow call only with concurrent dispatch.
    assert ping_at < slow_at
    assert REGEX_TIMEOUT_S <= slow_at < REGEX_TIMEOUT_S + 1.5  # allow scheduling, reaping and stdio after the deadline
    slow_payload = json.loads(slow["result"]["content"][0]["text"])
    assert slow_payload["results"][0]["reason"] == "regex timed out after 1000ms; simplify the pattern"
    quick_payload = json.loads(quick["result"]["content"][0]["text"])
    assert quick_payload["provider"] == "none"
    # A quiet host answers `not_found`; under heavy load the no-match worker's fixed 1 s
    # budget can be spent before its result lands, and ADR-0016 sanctions exactly that
    # timeout. Anything else (worker_error, a rejected pattern, a provider answer) fails.
    # Pool-level serialization is therefore NOT caught here — that contract is owned by
    # test_timeout_is_invalid_pattern_within_budget_while_other_calls_run in
    # tests/contract/test_extract.py; this layer owns dispatch, pinned by ping_at < slow_at above.
    if quick_payload["results"][0]["status"] != "not_found":
        assert quick_payload["results"][0]["status"] == "invalid_pattern"
        assert quick_payload["results"][0]["reason"] == REGEX_TIMEOUT_REASON

    assert returncode == 0
    assert "Traceback" not in stderr
    deadline = time.monotonic() + 5
    while any(alive(pid) for pid in workers_seen) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not {pid for pid in workers_seen if alive(pid)}
    assert not worker_pids(server.process.pid)
    for line in lines:
        assert json.loads(line)["jsonrpc"] == "2.0"


def test_cancelled_extract_reaps_worker_and_keeps_server_responsive() -> None:
    with StdioServer() as server:
        server.initialize()
        # A census after sending the slow call can consume its whole one-second budget
        # before cancellation. Record the warm pool first, then send the two frames in order.
        workers = worker_pids(server.process.pid)
        assert workers, f"server {server.process.pid} has no warm {WORKER_MODULE} workers"
        # Observe reaping before the ordinary request timeout could kill the worker.
        deadline = time.monotonic() + REGEX_TIMEOUT_S
        server.send(call(10, "jev_extract", SLOW))
        server.send(
            {
                "jsonrpc": "2.0",
                "method": "notifications/cancelled",
                "params": {"requestId": 10, "reason": "integration test"},
            }
        )
        survivors = {pid for pid in workers if alive(pid)}
        while survivors == workers and time.monotonic() < deadline:
            time.sleep(0.01)
            survivors = {pid for pid in workers if alive(pid)}
        assert time.monotonic() < deadline, "cancelled worker survived until the ordinary timeout"
        assert len(survivors) == len(workers) - 1, (
            f"server {server.process.pid} worker PIDs before cancel {workers}, surviving {survivors}"
        )

        quick = server.request(call(11, "jev_extract", NO_MATCH))
        quick_payload = json.loads(quick["result"]["content"][0]["text"])
        assert quick_payload["results"][0]["status"] == "not_found"
        server.close_stdin()
        returncode, stderr = server.wait()

    assert returncode == 0
    assert "Traceback" not in stderr
    assert not worker_pids(server.process.pid)


@pytest.mark.parametrize("name", ["jev_verify", "jev_nope"])
def test_errors_come_back_as_tool_results(name: str) -> None:
    with StdioServer() as server:
        server.initialize()
        reply = server.request(call(2, name, {"claims": ["c"], "evidence": "e"}))
        server.close_stdin()
        server.wait()
    result = reply["result"]
    assert result["isError"] is True
    text = result["content"][0]["text"]
    if name == "jev_nope":
        assert text == "MCP error -32602: Tool jev_nope not found"
    else:
        assert text.startswith("No Jev provider credentials found.")


def test_missing_arguments_are_reported_at_the_root() -> None:
    """The reference's zod parses `undefined` and reports the root; `{}` still names each field."""
    with StdioServer() as server:
        server.initialize()
        missing = server.request({"jsonrpc": "2.0", "id": 10, "method": "tools/call", "params": {"name": "jev_screen"}})
        empty = server.request(call(11, "jev_screen", {}))
        server.close_stdin()
        server.wait()
    prefix = "MCP error -32602: Input validation error: Invalid arguments for tool jev_screen: "
    result = missing["result"]
    assert result["isError"] is True
    assert result["content"][0] == {"type": "text", "text": prefix + "Required"}
    assert json.loads(result["content"][1]["text"]) == {"code": "invalid_arguments"}
    assert result["structuredContent"] == {"code": "invalid_arguments"}
    assert empty["result"]["content"][0]["text"] == prefix + "Required at text"


def test_null_arguments_are_a_protocol_error() -> None:
    """The reference's request schema rejects `"arguments": null` before any tool: -32603 with zod's issue list."""
    with StdioServer() as server:
        server.initialize()
        reply = server.request(
            {"jsonrpc": "2.0", "id": 10, "method": "tools/call", "params": {"name": "jev_screen", "arguments": None}}
        )
        server.close_stdin()
        server.wait()
    issue = (
        '[\n  {\n    "expected": "record",\n    "code": "invalid_type",\n    "path": [\n      "params",\n'
        '      "arguments"\n    ],\n    "message": "Invalid input: expected record, received null"\n  }\n]'
    )
    assert reply == {"jsonrpc": "2.0", "id": 10, "error": {"code": -32603, "message": issue}}
