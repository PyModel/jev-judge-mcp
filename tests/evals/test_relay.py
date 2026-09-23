"""The recording relay (`evals.relay`) behind both proxies, driven end to end through the fake server."""

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, cast

import pytest

from evals.ab import stream
from evals.ab.ledger import JEV_RUN_BOUND_USD
from evals.bench import gate
from evals.bench.proxy import BENCH_REQUEST_CAP
from evals.relay import HEADLINE_TOOLS, result_fields
from evals.spend import JEV_PUBLISHED_USD_PER_MTOK_INPUT
from tests.support.fake_mcp_server import FAKE_ERROR, RECEIVED
from tests.support.fixtures import iter_calls

REPO = Path(__file__).resolve().parents[2]
FAKE = str(REPO / "tests" / "support" / "fake_mcp_server.py")
PROXIES = ("evals.ab.proxy", "evals.bench.proxy")


def _call(n: int | None, name: str = "jev_verify") -> dict[str, Any]:
    body: dict[str, Any] = {"jsonrpc": "2.0", "method": "tools/call", "params": {"name": name, "arguments": {"k": n}}}
    return body if n is None else {**body, "id": n}


def _relay(proxy: str, log: Path, messages: list[Any]) -> tuple[list[Any], list[dict[str, Any]]]:
    """Run the proxy with its stdin held open; returns what reached the client before stdout closed, and the log."""
    child = subprocess.Popen(
        [sys.executable, "-m", proxy, str(log), "--", sys.executable, FAKE],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        cwd=REPO,
    )
    assert child.stdin is not None and child.stdout is not None
    child.stdin.write(b"".join((json.dumps(message) + "\n").encode() for message in messages))
    child.stdin.flush()
    out: list[bytes] = []
    reader = threading.Thread(target=lambda: out.append(child.stdout.read() if child.stdout else b""))
    reader.start()
    reader.join(timeout=30)
    try:
        assert not reader.is_alive(), "the relay never closed the client's stdout"
    finally:
        child.stdin.close()
        assert child.wait(timeout=30) == 0
    replies = [json.loads(line) for line in out[0].splitlines()]
    rows = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()] if log.exists() else []
    return replies, rows


def _received(log: Path) -> int:
    return log.with_suffix(".stderr").read_text(encoding="utf-8").splitlines().count(RECEIVED)


def _tools_call(message_id: int, params: object) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": message_id, "method": "tools/call", "params": params}


def _hang_server(pid: Path, log: Path, marker: Path, *, reads: int) -> str:
    """A child that records the log when it receives SIGTERM, so a test can see the row was already written."""
    reply = ""
    if reads:
        reply = (
            "sys.stdin.readline()\n" * reads
            + "sys.stdout.write(json.dumps({'jsonrpc': '2.0', 'id': 1, 'result': [1]}) + '\\n')\n"
            + "sys.stdout.flush()\n"
        )
    return (
        "import json, os, signal, sys, time\n"
        "from pathlib import Path\n"
        f"LOG = Path({str(log)!r})\n"
        f"MARKER = Path({str(marker)!r})\n"
        "def on_term(signum, frame):\n"
        "    text = LOG.read_text(encoding='utf-8') if LOG.exists() else ''\n"
        "    MARKER.write_text(text, encoding='utf-8')\n"
        "    os._exit(0)\n"
        "signal.signal(signal.SIGTERM, on_term)\n"
        f"Path({str(pid)!r}).write_text(str(os.getpid()))\n"
        f"{reply}"
        "time.sleep(60)\n"
    )


def _reap(pid_path: Path) -> None:
    if not pid_path.exists():
        return
    text = pid_path.read_text(encoding="utf-8").strip()
    if not text:
        return
    try:
        os.kill(int(text), 9)
    except ProcessLookupError:
        pass


def _proxy_argv(proxy: str, log: Path, server: Path) -> list[str]:
    return [sys.executable, "-m", proxy, str(log), "--", sys.executable, str(server)]


def _run_until_dead(argv: list[str], server_source: str, messages: list[Any], pid_path: Path) -> tuple[str, str]:
    """Run a relay until it exits. Returns stderr and the log text captured at SIGTERM."""
    server = pid_path.with_name("server.py")
    marker = pid_path.with_name("at-term.txt")
    server.write_text(server_source, encoding="utf-8")
    proc = subprocess.Popen(
        argv,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=REPO,
    )
    try:
        deadline = time.monotonic() + 30
        while not pid_path.exists():
            if proc.poll() is not None:
                _out, err = proc.communicate()
                raise AssertionError(f"proxy exited before the server wrote its pid\n{err.decode()}")
            if time.monotonic() > deadline:
                raise AssertionError("server pid was never written")
            time.sleep(0.02)
        assert proc.stdin is not None
        proc.stdin.write(b"".join((json.dumps(message) + "\n").encode() for message in messages))
        proc.stdin.flush()
        try:
            _out, err = proc.communicate(timeout=30)
        except subprocess.TimeoutExpired:
            proc.kill()
            _out, err = proc.communicate()
            raise AssertionError(f"relay did not exit\n{err.decode()}") from None
        captured = marker.read_text(encoding="utf-8") if marker.exists() else ""
        return err.decode(), captured
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)
        _reap(pid_path)


def _rows(text: str) -> list[dict[str, Any]]:
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def _assert_dead(pid_path: Path) -> None:
    with pytest.raises(ProcessLookupError):
        os.kill(int(pid_path.read_text(encoding="utf-8")), 0)


# --- T3: a call still pending at the server's EOF --------------------------------------------------


def test_p8_logs_a_call_left_pending_at_eof_as_unanswered(tmp_path: Path) -> None:
    log = tmp_path / "calls.jsonl"
    _, rows = _relay("evals.ab.proxy", log, [_call(1, "fake_exit")])
    assert [{k: v for k, v in row.items() if k != "ms"} for row in rows] == [
        {"tool": "fake_exit", "is_error": True, "unanswered": True}
    ]
    assert rows[0]["ms"] >= 0


def test_bench_logs_a_call_left_pending_at_eof_as_unanswered(tmp_path: Path) -> None:
    log = tmp_path / "calls.jsonl"
    _, rows = _relay("evals.bench.proxy", log, [_call(1, "fake_exit")])
    assert len(rows) == 1
    row = rows[0]
    assert {k: row[k] for k in ("seq", "tool", "arguments", "refused", "is_error", "unanswered", "text")} == {
        "seq": 0,
        "tool": "fake_exit",
        "arguments": {"k": 1},
        "refused": False,
        "is_error": True,
        "unanswered": True,
        "text": None,
    }
    assert row["t1"] >= row["t0"] and row["ms"] >= 0


@pytest.mark.parametrize("stream_result", [True, None], ids=["stream-error", "no-stream-result"])
def test_cross_check_counts_an_unanswered_call_as_errored(stream_result: bool | None) -> None:
    calls = [
        {"seq": 0, "tool": "jev_verify", "is_error": False, "refused": False},
        {"seq": 1, "tool": "jev_screen", "is_error": True, "refused": False, "unanswered": True},
    ]
    trace = stream.Trace()
    for n, (name, result) in enumerate((("mcp__jev__jev_verify", False), ("mcp__jev__jev_screen", stream_result))):
        trace.tool_uses.append(stream.ToolUse(name, {}, f"t{n}"))
        if result is not None:
            trace.tool_results[f"t{n}"] = result
    gate.cross_check(calls, trace)


def test_cross_check_still_stops_when_an_unanswered_call_succeeded_in_the_stream() -> None:
    calls = [{"seq": 0, "tool": "jev_screen", "is_error": True, "refused": False, "unanswered": True}]
    trace = stream.Trace()
    trace.tool_uses.append(stream.ToolUse("mcp__jev__jev_screen", {}, "t0"))
    trace.tool_results["t0"] = False
    with pytest.raises(gate.HarnessMismatchError):
        gate.cross_check(calls, trace)


# --- T7: a server-to-client request is never paired as a response ----------------------------------


@pytest.mark.parametrize("proxy", PROXIES)
def test_a_server_request_with_a_pending_id_is_relayed_not_paired(proxy: str, tmp_path: Path) -> None:
    log = tmp_path / "calls.jsonl"
    replies, rows = _relay(proxy, log, [_call(1, "fake_collide"), _call(2, "fake_exit")])
    assert replies[0] == {"jsonrpc": "2.0", "id": 1, "method": "sampling/createMessage", "params": {}}
    collide = [row for row in rows if row["tool"] == "fake_collide"]
    assert len(collide) == 1
    assert collide[0]["model"] == "jev-1.13.0" and collide[0]["is_error"] is False


# --- T8: nothing reaches the server around the bench cap -------------------------------------------


def test_bench_refuses_an_id_less_tools_call(tmp_path: Path) -> None:
    log = tmp_path / "calls.jsonl"
    _, rows = _relay("evals.bench.proxy", log, [_call(None), _call(1), _call(2, "fake_exit")])
    assert _received(log) == 2, "only the calls with an id reach the server"
    refused = [row for row in rows if row["refused"]]
    assert [(row["tool"], row["is_error"], row["arguments"]) for row in refused] == [("jev_verify", True, {"k": None})]


def test_bench_refuses_positional_params_without_sending_them(tmp_path: Path) -> None:
    """JSON-RPC permits array params; the bench cannot row or count them, so it answers itself."""
    positional = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": ["jev_verify", {"k": 1}]}
    log = tmp_path / "calls.jsonl"
    replies, rows = _relay("evals.bench.proxy", log, [positional, _call(2, "fake_exit")])
    assert _received(log) == 1, "the positional call never reaches the server"
    refused = [row for row in rows if row["refused"]]
    assert [row["text"] for row in refused] == ["bench proxy: a tools/call with positional params is not sent"]
    assert replies[0]["result"]["isError"] is True


def test_p8_forwards_positional_params_without_a_row(tmp_path: Path) -> None:
    """The A/B recorder logs names only: a positional call has none, so it is forwarded unrecorded."""
    positional = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": ["jev_verify", {"k": 1}]}
    log = tmp_path / "calls.jsonl"
    _, rows = _relay("evals.ab.proxy", log, [positional, _call(2, "fake_exit")])
    assert [row["tool"] for row in rows] == ["fake_exit"]


def test_p8_logs_a_displaced_duplicate_id_row(tmp_path: Path) -> None:
    """A reused request id logs the call it displaced instead of silently dropping its row."""
    log = tmp_path / "calls.jsonl"
    _, rows = _relay("evals.ab.proxy", log, [_call(7, "jev_verify"), _call(7, "jev_review"), _call(8, "fake_exit")])
    assert [(row["tool"], row.get("duplicate_id", False)) for row in rows] == [
        ("jev_verify", True),
        ("jev_review", False),
        ("fake_exit", False),
    ]
    displaced = rows[0]
    assert displaced["is_error"] is True


def test_bench_refuses_a_batch_array_with_a_tools_call(tmp_path: Path) -> None:
    log = tmp_path / "calls.jsonl"
    replies, rows = _relay(
        "evals.bench.proxy",
        log,
        [[_call(1), _call(2, "jev_gate"), {"jsonrpc": "2.0", "id": 3, "method": "ping"}], _call(4, "fake_exit")],
    )
    assert _received(log) == 1, "no call from the batch reaches the server"
    batch = cast(list[dict[str, Any]], replies[0])
    assert isinstance(batch, list)
    assert [reply["id"] for reply in batch] == [1, 2, 3]
    assert all(reply["result"]["isError"] is True for reply in batch[:2])
    assert batch[2]["error"]["code"] == -32600
    refused = sorted((row["tool"], row["seq"]) for row in rows if row["refused"])
    assert refused == [("jev_gate", 1), ("jev_verify", 0)]


def test_bench_cap_fits_the_jev_run_bound() -> None:
    full_request_tokens = 64_000
    worst = BENCH_REQUEST_CAP * full_request_tokens * JEV_PUBLISHED_USD_PER_MTOK_INPUT / 1_000_000
    assert worst <= JEV_RUN_BOUND_USD


# --- T30: drift between the two proxies -------------------------------------------------------------


def test_bench_logs_the_message_of_a_json_rpc_error(tmp_path: Path) -> None:
    log = tmp_path / "calls.jsonl"
    replies, rows = _relay("evals.bench.proxy", log, [_call(1, "fake_error"), _call(2, "fake_exit")])
    assert replies[0]["error"] == FAKE_ERROR
    row = next(row for row in rows if row["tool"] == "fake_error")
    assert row["is_error"] is True and row["text"] == FAKE_ERROR["message"]


def test_p8_logs_no_text_for_a_json_rpc_error(tmp_path: Path) -> None:
    log = tmp_path / "calls.jsonl"
    _, rows = _relay("evals.ab.proxy", log, [_call(1, "fake_error"), _call(2, "fake_exit")])
    row = next(row for row in rows if row["tool"] == "fake_error")
    assert set(row) == {"tool", "ms", "is_error"} and row["is_error"] is True
    assert str(FAKE_ERROR["message"]) not in log.read_text(encoding="utf-8")


@pytest.mark.parametrize("proxy", PROXIES)
def test_client_stdout_closes_when_the_server_exits(proxy: str, tmp_path: Path) -> None:
    replies, _ = _relay(proxy, tmp_path / "calls.jsonl", [_call(1), _call(2, "fake_exit")])
    assert [reply["id"] for reply in replies] == [1]


# --- T9: each tool's headline Action, not only a top-level `action` ---------------------------------


def _result(body: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": 1, "result": {"content": [{"type": "text", "text": json.dumps(body)}]}}


@pytest.mark.parametrize(
    ("body", "headline"),
    [
        ({"tool": "jev_review", "action": "auto"}, "auto"),
        ({"tool": "jev_gate", "action": "escalate", "review": {"action": "auto"}}, "escalate"),
        ({"tool": "jev_screen", "recommendation": {"action": "review"}}, "review"),
        ({"tool": "jev_screen", "recommendation": {"action": "block"}}, None),
        ({"tool": "jev_compare", "overall": {"decision": "review"}, "aspects": [{"decision": "escalate"}]}, "review"),
        (
            {"tool": "jev_verify", "results": [{"action": "auto"}, {"action": "escalate"}, {"action": "review"}]},
            "escalate",
        ),
        ({"tool": "jev_classify", "results": [{"decision": "auto"}, {"decision": "review"}]}, "review"),
        ({"tool": "jev_extract", "results": [{"status": "not_found"}, {"status": "auto"}]}, "auto"),
        ({"tool": "jev_extract", "results": [{"status": "not_found"}]}, None),
        ({"tool": "jev_find", "exists_verdict": "partial"}, None),
    ],
)
def test_result_fields_log_the_tool_headline_action(body: dict[str, Any], headline: str | None) -> None:
    assert result_fields(_result(body))["headline_action"] == headline


def test_every_action_bearing_tool_yields_a_headline_from_recorded_output() -> None:
    headlines: dict[str, set[str | None]] = {}
    for call in iter_calls():
        fields = result_fields({"result": call.payload["result"]})
        if "headline_action" in fields:
            headlines.setdefault(str(call.payload["tool"]), set()).add(fields["headline_action"])
    assert all(value in {None, "auto", "review", "escalate"} for values in headlines.values() for value in values)
    with_headline = {tool for tool, values in headlines.items() if values - {None}}
    assert with_headline == set(HEADLINE_TOOLS)


# --- FIX-06: log pending rows, then terminate the child --------------------------------------------

# response() raises, and close() logs one pending call before raising too. The relay must keep the other.
_CLOSE_RAISES = """
import json
import sys

from evals.relay import pending_key, serve

class Recorder:
    def __init__(self, log):
        self.log = log
        self.pending = {}

    def request(self, message):
        if isinstance(message, dict) and message.get("method") == "tools/call" and "id" in message:
            params = message.get("params") or {}
            name = params.get("name") if isinstance(params, dict) else "tool"
            self.pending[pending_key(message["id"])] = str(name)
        return None

    def response(self, message):
        raise RuntimeError("response failed")

    def close(self):
        if self.pending:
            _key, tool = self.pending.popitem()
            self.log.write(json.dumps({"seq": 0, "tool": tool, "unanswered": True, "is_error": True}) + "\\n")
            self.log.flush()
        raise RuntimeError("close failed")

raise SystemExit(serve(sys.argv[1:], "tests.close-raises", Recorder))
"""


@pytest.mark.parametrize("proxy", PROXIES)
def test_response_raise_logs_the_recorders_unanswered_row_and_kills_the_child(proxy: str, tmp_path: Path) -> None:
    """recorder.response raises. The recorder's own unanswered row is on disk before SIGTERM."""
    log = tmp_path / "calls.jsonl"
    pid = tmp_path / "server.pid"
    hidden = "response-value"
    server = pid.with_name("server.py")
    try:
        err, at_term = _run_until_dead(
            _proxy_argv(proxy, log, server),
            _hang_server(pid, log, pid.with_name("at-term.txt"), reads=1),
            [_tools_call(1, {"name": "jev_verify", "arguments": {"k": hidden}})],
            pid,
        )
        rows = _rows(at_term)
        assert len(rows) == 1
        row = rows[0]
        assert row["unanswered"] is True and row["is_error"] is True and "recorder_error" not in row
        assert row["tool"] == "jev_verify"
        seqs = [item["seq"] for item in rows if "seq" in item]
        assert len(seqs) == len(set(seqs))
        if proxy == "evals.bench.proxy":
            assert row["seq"] == 0 and row["arguments"] == {"k": hidden} and row["text"] is None
        else:
            assert "seq" not in row and "arguments" not in row and hidden not in at_term
        assert "Traceback" in err
        _assert_dead(pid)
    finally:
        _reap(pid)


def test_recorder_close_raise_writes_fallback_rows_and_kills_the_child(tmp_path: Path) -> None:
    """recorder.close also raises. The call it did not log is a fallback row, and seq does not collide."""
    log = tmp_path / "calls.jsonl"
    pid = tmp_path / "server.pid"
    server = pid.with_name("server.py")
    try:
        _err, at_term = _run_until_dead(
            [sys.executable, "-c", _CLOSE_RAISES, str(log), "--", sys.executable, str(server)],
            _hang_server(pid, log, pid.with_name("at-term.txt"), reads=2),
            [
                _tools_call(1, {"name": "jev_verify", "arguments": {"k": 1}}),
                _tools_call(2, {"name": "jev_screen", "arguments": {"k": 2}}),
            ],
            pid,
        )
        rows = _rows(at_term)
        assert {row["tool"] for row in rows} == {"jev_verify", "jev_screen"}
        assert len(rows) == 2
        assert sorted(row["seq"] for row in rows) == [0, 1]
        fallback = [row for row in rows if row.get("recorder_error") is True]
        assert len(fallback) == 1 and fallback[0]["unanswered"] is True and "arguments" not in fallback[0]
        assert "recorder_error" in at_term
        _assert_dead(pid)
    finally:
        _reap(pid)
