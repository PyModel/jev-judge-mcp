"""The server over stdio, spawned as a subprocess (ROADMAP P1 acceptance)."""

import json
import os
import signal
import sys
import time
from pathlib import Path

import pytest

from jev_judge_mcp.identity import reported_version
from tests.support.stdio import PROTOCOL_VERSION, StdioServer, server_command


def test_initialize_negotiates_2025_06_18() -> None:
    with StdioServer() as server:
        reply = server.initialize()
        server.close_stdin()
        returncode, _ = server.wait()
    assert reply["result"]["protocolVersion"] == PROTOCOL_VERSION
    assert reply["result"]["serverInfo"]["name"] == "jev-mcp"
    assert returncode == 0


def test_initialize_reports_the_build_identity() -> None:
    """`serverInfo.version` and the one startup log line are the checkout identity (ADR-0054)."""
    identity = reported_version()
    with StdioServer() as server:
        reply = server.initialize()
        server.close_stdin()
        returncode, stderr = server.wait()
    assert returncode == 0, stderr
    info = reply["result"]["serverInfo"]
    assert info["name"] == "jev-mcp"
    assert info["version"] == identity
    assert stderr.count(f"identity jev-mcp {identity}") == 1


def test_an_unexpanded_credential_reference_is_warned_once_by_name() -> None:
    """A harness that passed `${TYPESAFE_API_KEY}` through unexpanded is told so at startup (ADR-0046)."""
    with StdioServer(env={"TYPESAFE_API_KEY": "${TYPESAFE_API_KEY}"}) as server:
        server.initialize()
        server.close_stdin()
        returncode, stderr = server.wait()
    assert returncode == 0, stderr
    assert stderr.count("TYPESAFE_API_KEY holds an unexpanded reference and counts as unset") == 1


def test_stdout_carries_only_protocol_frames() -> None:
    with StdioServer() as server:
        server.initialize()
        server.request({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        server.request({"jsonrpc": "2.0", "id": 3, "method": "ping"})
        server.request({"jsonrpc": "2.0", "id": 4, "method": "no/such/method"})
        server.request({"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {"name": "nope"}})
        server.close_stdin()
        returncode, _ = server.wait()
        lines = server.stdout_lines
    assert returncode == 0
    assert len(lines) >= 5
    for line in lines:
        assert line.endswith(b"\n")
        frame = json.loads(line)
        assert frame["jsonrpc"] == "2.0"
        assert ("id" in frame and ("result" in frame or "error" in frame)) or "method" in frame


@pytest.mark.parametrize("idle", [0.0, 0.5], ids=["busy", "idle"])
@pytest.mark.parametrize("signum", [signal.SIGINT, signal.SIGTERM], ids=["SIGINT", "SIGTERM"])
def test_signal_exits_cleanly(signum: signal.Signals, idle: float) -> None:
    """`idle`: a signal while the stdin reader is parked in `readline()` must still exit."""
    with StdioServer() as server:
        server.initialize()
        server.request({"jsonrpc": "2.0", "id": 2, "method": "ping"})
        time.sleep(idle)
        server.process.send_signal(signum)
        returncode, stderr = server.wait()
    assert returncode == 0
    assert "Traceback" not in stderr
    assert f"received {signum.name}" in stderr


def test_stdin_eof_exits_cleanly() -> None:
    with StdioServer() as server:
        server.close_stdin()
        returncode, stderr = server.wait()
    assert returncode == 0
    assert "Traceback" not in stderr


# A handler that prints: the SDK diverts fd 1 to stderr while serving, so the print must miss the wire.
_PRINTING_SERVER = """
import anyio
from jev_judge_mcp.server import JevMCPServer, configure_logging, serve
from jev_judge_mcp.settings import load_settings
from jev_judge_mcp.tools import Runtime, Toolset

class PrintingServer(JevMCPServer):
    async def list_tools(self):
        print("CONTAMINANT", flush=True)
        return []

settings = load_settings()
configure_logging(settings.log_level)
toolset = Toolset(Runtime(settings), [])
anyio.run(serve, PrintingServer(toolset=toolset, log_level=settings.log_level), settings)
"""


def test_handler_prints_go_to_stderr() -> None:
    with StdioServer([sys.executable, "-c", _PRINTING_SERVER]) as server:
        server.initialize()
        server.request({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        server.close_stdin()
        returncode, stderr = server.wait()
        lines = server.stdout_lines
    assert returncode == 0
    assert "CONTAMINANT" in stderr
    for line in lines:
        assert json.loads(line)["jsonrpc"] == "2.0"


def test_a_server_may_log_more_stderr_than_the_pipe_buffer() -> None:
    """StdioServer drains stderr while the server runs.

    A DEBUG-level server against a chatty peer writes more than the 64 KiB pipe buffer of
    stderr; an undrained pipe then blocks the server's shutdown writes and `wait()` sees a
    hang, not an exit — exactly the reflected-credentials deadlock, where the server sat at
    98% of the buffer on main and one added traceback frame tipped it over.
    """
    script = (
        "import sys\n"
        "block = b'x' * 8192\n"
        "for _ in range(48):\n"
        "    sys.stderr.buffer.write(block)\n"
        "    sys.stderr.buffer.flush()\n"
        "sys.stdin.read()\n"
    )
    with StdioServer(command=[sys.executable, "-c", script]) as server:
        server.close_stdin()
        returncode, stderr = server.wait(timeout=30)
    assert returncode == 0
    assert len(stderr) == 48 * 8192


def test_buffered_frames_are_judged_at_arrival() -> None:
    script = "print('{\"id\":1}'); print('{\"id\":2}')"
    before_start = time.monotonic()
    with StdioServer([sys.executable, "-c", script]) as server:
        assert server.wait()[0] == 0  # both frames have reached the reader
        after_arrival = time.monotonic()
        arrived, frame = server.receive_until(after_arrival)
        assert before_start <= arrived <= after_arrival
        assert frame == {"id": 1}
        with pytest.raises(TimeoutError):
            server.receive_until(before_start)


def test_deferred_replies_keep_their_arrival_time() -> None:
    peer = """
import json, sys
for line in sys.stdin:
    request = json.loads(line)
    print(json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": {}}), flush=True)
"""
    before_start = time.monotonic()
    with StdioServer([sys.executable, "-c", peer]) as server:
        for ident in (2, 3):
            server.send({"jsonrpc": "2.0", "id": ident, "method": "ping"})
        assert server.request({"jsonrpc": "2.0", "id": 1, "method": "ping"})["id"] == 1
        after_collection = time.monotonic()
        arrived, reply = server.receive_until(after_collection)
        assert before_start <= arrived <= after_collection
        assert reply["id"] == 2
        with pytest.raises(TimeoutError):
            server.receive_until(before_start)


def test_a_bare_carriage_return_is_json_whitespace() -> None:
    with StdioServer() as server:
        server.initialize()
        stdin = server.process.stdin
        assert stdin is not None
        stdin.write(b'{"jsonrpc":"2.0",\r"id":2,"method":"ping"}\n')
        stdin.flush()
        assert server.receive(timeout=2) == {"jsonrpc": "2.0", "id": 2, "result": {}}


@pytest.mark.parametrize(
    ("args", "body"),
    [
        ([], ""),
        (["doctor"], ""),
        (["setup"], ""),
        (["hook", "gate"], '{"tool_name":"Bash","tool_input":{"command":"pwd"}}'),
        (["hook", "screen"], '{"tool_name":"Bash","tool_response":"output"}'),
        (["completion-hook"], ""),
    ],
    ids=["server", "doctor", "setup", "gate-hook", "screen-hook", "completion-hook"],
)
@pytest.mark.parametrize(
    ("variable", "value", "accepted"),
    [
        ("JEV_MCP_CACHE", "${JEV_MCP_CACHE}", "true"),
        ("JEV_MCP_HTTP_PORT", "abc-invalid-value", "integer"),
    ],
)
def test_invalid_settings_report_one_line(args: list[str], body: str, variable: str, value: str, accepted: str) -> None:
    with StdioServer([*server_command(), *args], env={variable: value}) as server:
        if body:
            assert server.process.stdin is not None
            server.process.stdin.write(body.encode())
        server.close_stdin()
        returncode, stderr = server.wait()
    assert returncode == (0 if args and args[0] in ("hook", "completion-hook") else 1)
    assert server.stdout_lines == []
    assert len(stderr.splitlines()) == 1
    assert variable in stderr and accepted in stderr and value not in stderr


@pytest.mark.parametrize("args", [["doctor"], ["hook", "compact-cut"]], ids=["doctor", "compact"])
def test_local_config_and_transcript_ignore_fifos(args: list[str], tmp_path: Path) -> None:
    claude = tmp_path / ".claude"
    claude.mkdir()
    fifo = claude / "settings.json"
    os.mkfifo(fifo)
    with StdioServer([*server_command(), *args], env={"HOME": str(tmp_path)}, cwd=tmp_path) as server:
        if args[0] == "hook":
            assert server.process.stdin is not None
            server.process.stdin.write(
                json.dumps(
                    {
                        "hook_event_name": "SessionStart",
                        "source": "compact",
                        "transcript_path": str(fifo),
                    }
                ).encode()
            )
        server.close_stdin()
        returncode, stderr = server.wait(timeout=4)
    if args[0] == "doctor":
        assert returncode == 1
        assert b"no allow rules" in b"".join(server.stdout_lines)
    else:
        assert returncode == 0 and not server.stdout_lines
    assert stderr == ""
