"""The server over stdio, spawned as a subprocess (ROADMAP P1 acceptance)."""

import json
import signal
import sys
import time

import pytest

from jev_judge_mcp.identity import reported_version
from tests.support.stdio import PROTOCOL_VERSION, StdioServer


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
