"""Tools over the real stdio transport: a regex that times out never holds up other calls, the wire
stays protocol-only while worker processes run, and shutdown leaves no worker behind."""

import json
import os
import shlex
import subprocess
import sys
import time
from typing import Any

import pytest

from tests.support.stdio import StdioServer

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


WORKER_MODULE = "jev_judge_mcp.extract.worker"


def process_snapshot() -> dict[int, tuple[int, list[str]]]:
    listing = subprocess.run(
        ["ps", "-A", "-ww", "-o", "pid=,ppid=,command="], capture_output=True, text=True, check=True
    )
    processes: dict[int, tuple[int, list[str]]] = {}
    for line in listing.stdout.splitlines():
        fields = line.split(None, 2)
        if len(fields) != 3:
            continue
        try:
            pid, ppid = int(fields[0]), int(fields[1])
            argv = shlex.split(fields[2])
        except ValueError:
            continue
        if argv:
            processes[pid] = (ppid, argv)
    return processes


def is_worker(argv: list[str]) -> bool:
    return any(argv[index : index + 2] == ["-m", WORKER_MODULE] for index in range(len(argv) - 1))


def worker_pids(root_pid: int, processes: dict[int, tuple[int, list[str]]] | None = None) -> set[int]:
    """Workers below root, including through launchers, matched by the exact `-m MODULE` argv pair."""
    table = processes if processes is not None else process_snapshot()
    children: dict[int, list[int]] = {}
    for pid, (ppid, _) in table.items():
        children.setdefault(ppid, []).append(pid)
    descendants: set[int] = set()
    pending = [root_pid]
    while pending:
        parent = pending.pop()
        for child in children.get(parent, []):
            if child not in descendants:
                descendants.add(child)
                pending.append(child)
    return {pid for pid in descendants if pid in table and is_worker(table[pid][1])}


def orphan_worker_pids() -> set[int]:
    return {pid for pid, (ppid, argv) in process_snapshot().items() if ppid == 1 and is_worker(argv)}


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def ps_snapshot() -> str:
    """The whole process listing, for the failure message: what exists when an assert about
    processes fails, on any runner."""
    return subprocess.run(
        ["ps", "-A", "-ww", "-o", "pid=,ppid=,command="], capture_output=True, text=True, check=True
    ).stdout


def test_worker_census_ignores_a_decoy_command_line() -> None:
    decoy = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)", WORKER_MODULE],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 2
        processes = process_snapshot()
        while decoy.pid not in processes and time.monotonic() < deadline:
            time.sleep(0.01)
            processes = process_snapshot()
        assert decoy.pid in processes
        assert WORKER_MODULE in processes[decoy.pid][1]
        assert is_worker([sys.executable, "-m", WORKER_MODULE])
        assert not is_worker([sys.executable, "-c", WORKER_MODULE])
        with StdioServer() as server:
            server.initialize()
            processes = process_snapshot()
            assert decoy.pid not in worker_pids(server.process.pid, processes)
            server.close_stdin()
            returncode, _ = server.wait()
        assert returncode == 0
    finally:
        if decoy.poll() is None:
            decoy.terminate()
            decoy.wait(timeout=5)


def test_timeout_does_not_block_other_calls_and_shutdown_reaps_workers() -> None:
    with StdioServer() as server:
        server.initialize()
        started = time.monotonic()
        server.send(call(10, "jev_extract", SLOW))
        server.send(call(11, "jev_extract", NO_MATCH))
        replies: dict[int, tuple[float, dict[str, Any]]] = {}
        while len(replies) < 2:
            reply = server.receive()
            if "id" in reply:
                replies[reply["id"]] = (time.monotonic() - started, reply)
        workers = worker_pids(server.process.pid)
        server.close_stdin()
        returncode, stderr = server.wait()
        lines = server.stdout_lines

    quick_at, quick = replies[11]
    slow_at, slow = replies[10]
    assert quick_at < slow_at
    assert quick_at < 1
    assert slow_at < 2.5  # the 1 s deadline, a cold worker start, and host load
    slow_payload = json.loads(slow["result"]["content"][0]["text"])
    assert slow_payload["results"][0]["reason"] == "regex timed out after 1000ms; simplify the pattern"
    quick_payload = json.loads(quick["result"]["content"][0]["text"])
    assert quick_payload["provider"] == "none"
    assert quick_payload["results"][0]["status"] == "not_found"

    assert returncode == 0
    assert "Traceback" not in stderr
    assert workers, f"the no-match call left an idle worker; ps:\n{ps_snapshot()}"
    deadline = time.monotonic() + 5
    while any(alive(pid) for pid in workers) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not any(alive(pid) for pid in workers)
    for line in lines:
        assert json.loads(line)["jsonrpc"] == "2.0"


def test_cancelled_extract_reaps_worker_and_keeps_server_responsive() -> None:
    orphans_before = orphan_worker_pids()
    with StdioServer() as server:
        server.initialize()
        server.send(call(10, "jev_extract", SLOW))
        deadline = time.monotonic() + 5
        workers = worker_pids(server.process.pid)
        while not workers and time.monotonic() < deadline:
            time.sleep(0.02)
            workers = worker_pids(server.process.pid)
        assert workers, f"extract did not start a worker; ps:\n{ps_snapshot()}"
        time.sleep(0.05)

        server.send(
            {
                "jsonrpc": "2.0",
                "method": "notifications/cancelled",
                "params": {"requestId": 10, "reason": "integration test"},
            }
        )
        deadline = time.monotonic() + 0.75
        while any(alive(pid) for pid in workers) and time.monotonic() < deadline:
            time.sleep(0.01)
        assert not any(alive(pid) for pid in workers), f"cancel left a worker alive; ps:\n{ps_snapshot()}"

        quick = server.request(call(11, "jev_extract", NO_MATCH))
        quick_payload = json.loads(quick["result"]["content"][0]["text"])
        assert quick_payload["results"][0]["status"] == "not_found"
        server.close_stdin()
        returncode, stderr = server.wait()

    assert returncode == 0
    assert "Traceback" not in stderr
    assert not (orphan_worker_pids() - orphans_before), f"cancel left an orphan worker; ps:\n{ps_snapshot()}"


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
    assert missing["result"] == {"content": [{"type": "text", "text": prefix + "Required"}], "isError": True}
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
