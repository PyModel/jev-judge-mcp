"""Spawn the server as a subprocess and speak raw newline-delimited JSON-RPC to it.

Raw frames, not an SDK client: the acceptance checks are about what goes over the wire.
"""

import json
import os
import queue
import shlex
import subprocess
import sys
import threading
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Self

REPO_ROOT = Path(__file__).resolve().parents[2]
PROTOCOL_VERSION = "2025-06-18"
TIMEOUT = 15.0

# Provider variables are scrubbed so the developer's shell cannot leak into a test run.
_SCRUBBED_ENV = (
    "JEV_PROVIDER",
    "JEV_MCP_MODEL",
    "TYPESAFE_API_KEY",
    "TYPESAFE_BASE_URL",
    "OPENROUTER_API_KEY",
    "JEV_CLOUDFLARE_API_TOKEN",
    "CLOUDFLARE_API_TOKEN",
    "CLOUDFLARE_ACCOUNT_ID",
    "AI_GATEWAY_API_KEY",
    "JEV_API_KEY",
    "JEV_API_BASE_URL",
    "JEV_MCP_TRANSPORT",
    "JEV_MCP_KEY_FILE",
    "JEV_MCP_CACHE",
    "JEV_MCP_CACHE_DIR",
    "JEV_MCP_CACHE_MAX_ENTRIES",
    "JEV_MCP_CACHE_TTL_SECONDS",
    "JEV_MCP_HTTP_HOST",
    "JEV_MCP_HTTP_PORT",
    "JEV_MCP_HTTP_TOKEN",
    "JEV_MCP_LOG_LEVEL",
    "JEV_MCP_MAX_INFLIGHT",
    "JEV_MCP_TELEMETRY_PAYLOADS",
)

INITIALIZE: dict[str, Any] = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": PROTOCOL_VERSION,
        "capabilities": {},
        "clientInfo": {"name": "jev-mcp-tests", "version": "0"},
    },
}
INITIALIZED: dict[str, Any] = {"jsonrpc": "2.0", "method": "notifications/initialized"}


def server_command() -> list[str]:
    """`JEV_MCP_SERVER_CMD` overrides the command, e.g. `uvx --from . jev-judge-mcp` in the smoke stage."""
    override = os.environ.get("JEV_MCP_SERVER_CMD")
    if override:
        return shlex.split(override)
    return [sys.executable, "-m", "jev_judge_mcp"]


def server_env(extra: Mapping[str, str] | None = None) -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if key not in _SCRUBBED_ENV}
    # No test may resolve a key file the developer's machine happens to carry (ADR-0046): the
    # default points at a path that cannot exist, and an explicit extra still wins.
    env.setdefault("JEV_MCP_KEY_FILE", "/nonexistent/jev-mcp-key")
    env.update(extra or {})
    return env


class StdioServer:
    def __init__(self, command: Sequence[str] | None = None, env: Mapping[str, str] | None = None) -> None:
        self.process = subprocess.Popen(
            list(command or server_command()),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=server_env(env),
            cwd=REPO_ROOT,
        )
        self.stdout_lines: list[bytes] = []
        self._lines: queue.Queue[bytes | None] = queue.Queue()
        self._reader = threading.Thread(target=self._read_stdout, daemon=True)
        self._reader.start()
        # stderr is drained while the server runs: a DEBUG-level server can log more than the
        # pipe buffer (64 KiB), and an unread stderr pipe blocks the server's writes at shutdown
        # — the hang looks like a server that never exits, not like a full pipe.
        self.stderr_chunks: list[bytes] = []
        self._stderr_reader = threading.Thread(target=self._drain_stderr, daemon=True)
        self._stderr_reader.start()

    def _drain_stderr(self) -> None:
        assert self.process.stderr is not None
        while True:
            chunk = self.process.stderr.read(4096)
            if not chunk:
                return
            self.stderr_chunks.append(chunk)

    def _read_stdout(self) -> None:
        assert self.process.stdout is not None
        for line in self.process.stdout:
            self.stdout_lines.append(line)
            self._lines.put(line)
        self._lines.put(None)

    def send(self, message: Mapping[str, Any]) -> None:
        assert self.process.stdin is not None
        self.process.stdin.write(json.dumps(message).encode() + b"\n")
        self.process.stdin.flush()

    def receive(self, timeout: float = TIMEOUT) -> dict[str, Any]:
        line = self._lines.get(timeout=timeout)
        if line is None:
            raise EOFError("server closed stdout")
        message: dict[str, Any] = json.loads(line)
        return message

    def request(self, message: Mapping[str, Any]) -> dict[str, Any]:
        """Send a request and return the response with its id, skipping notifications."""
        self.send(message)
        while True:
            reply = self.receive()
            if reply.get("id") == message["id"]:
                return reply

    def initialize(self) -> dict[str, Any]:
        reply = self.request(INITIALIZE)
        self.send(INITIALIZED)
        return reply

    def close_stdin(self) -> None:
        assert self.process.stdin is not None
        self.process.stdin.close()

    def wait(self, timeout: float = TIMEOUT) -> tuple[int, str]:
        """Wait for exit; return the exit code and all of stderr (drained as it was written)."""
        try:
            returncode = self.process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait()
            raise
        self._reader.join(timeout=timeout)
        self._stderr_reader.join(timeout=timeout)
        return returncode, b"".join(self.stderr_chunks).decode()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        if self.process.poll() is None:
            self.process.kill()
            self.process.wait()
        for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
            if stream is not None:
                stream.close()
