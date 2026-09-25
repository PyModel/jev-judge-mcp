"""Tools over the real stdio transport: a regex that times out never holds up other calls, the wire
stays protocol-only while worker processes run, and shutdown leaves no worker behind."""

import json
import queue
import subprocess
import sys
import time
from typing import Any

import pytest

from jev_judge_mcp.extract.candidates import REGEX_TIMEOUT_REASON
from tests.support.stdio import StdioServer
from tests.support.workers import (
    WORKER_MODULE,
    alive,
    is_worker,
    orphan_worker_pids,
    process_snapshot,
    ps_snapshot,
    worker_pids,
)

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
        # Worker-free probe: `ping` needs no regex worker, so under concurrent dispatch it
        # answers in milliseconds while the slow call cannot reply before its 1 s deadline.
        server.send({"jsonrpc": "2.0", "id": 12, "method": "ping"})
        replies: dict[int, tuple[float, dict[str, Any]]] = {}
        workers_seen: set[int] = set()
        window = time.monotonic() + 10
        while len(replies) < 3 and time.monotonic() < window:
            # The pattern must run in a real worker process (ADR-0004), but worker spawn latency
            # is unbounded host load, so the census polls across the whole window instead of
            # sampling one instant. The pool rests empty whenever it pleases between demands:
            # a slot whose deadline ran out is killed and replaced only on the next demand
            # (extract/worker.py), so "an idle worker exists right now" is not the contract.
            workers_seen |= worker_pids(server.process.pid)
            try:
                reply = server.receive(timeout=0.1)
            except queue.Empty:
                continue
            if "id" in reply:
                replies[reply["id"]] = (time.monotonic() - started, reply)
        census = ps_snapshot()  # the table as of the census; after shutdown it shows nothing
        server.close_stdin()
        returncode, stderr = server.wait()
        lines = server.stdout_lines

    assert len(replies) == 3, f"the calls never all replied inside the window; ps:\n{census}"
    quick_at, quick = replies[11]
    slow_at, slow = replies[10]
    ping_at = replies[12][0]
    # Blocked-versus-unblocked by a wide margin, not a millisecond order race: serialized
    # dispatch or a stalled event loop would admit the no-match call only after the slow
    # call finished, and its fresh 1 s budget would still answer `not_found` at ~1.1-1.5 s
    # (passing any absolute bound); the worker-free ping, however, would wait behind the
    # slow call's whole handling and reply after it. Concurrent dispatch answers ping while
    # the slow regex is still spinning.
    assert ping_at < slow_at
    # Independent calls have no contractual reply order beyond that, and under load both
    # extract replies land near their own 1 s deadlines.
    assert quick_at < 2.5  # the no-match call's own 1 s deadline, a cold worker start, and host load
    assert slow_at < 2.5  # the 1 s deadline, a cold worker start, and host load
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
    assert workers_seen, f"no worker process ever served the calls; ps at census:\n{census}"
    deadline = time.monotonic() + 5
    while any(alive(pid) for pid in workers_seen) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not any(alive(pid) for pid in workers_seen)
    for line in lines:
        assert json.loads(line)["jsonrpc"] == "2.0"


def test_cancelled_extract_reaps_worker_and_keeps_server_responsive() -> None:
    orphans_before = orphan_worker_pids()
    with StdioServer() as server:
        server.initialize()
        # The served pool starts warm (ADR-0058): initialize only replies after serve has filled
        # it, and the slow call reuses a slot that already exists, so there is no spawn to wait
        # for - the census below is the warm pool, the cancelled call's worker among them. The
        # cancel frame follows the call frame on the same wire, so by the time it is read the
        # slow call is inside find() holding that slot.
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
        while all(alive(pid) for pid in workers) and time.monotonic() < deadline:
            time.sleep(0.01)
        survivors = {pid for pid in workers if alive(pid)}
        assert len(survivors) == len(workers) - 1, (
            f"cancel did not reap exactly the cancelled call's worker; ps:\n{ps_snapshot()}"
        )

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
    result = missing["result"]
    assert result["isError"] is True
    assert result["content"] == [{"type": "text", "text": prefix + "Required"}]
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
