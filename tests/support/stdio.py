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
import time
from collections.abc import Mapping, Sequence
from contextlib import suppress
from pathlib import Path
from typing import Any, Self

from tests.support.environment import SETTINGS_ENV as _SCRUBBED_ENV
from tests.support.rpc_replies import REPLY_TIMEOUT, ReplyStream, TimedReply, pipe_chunks

REPO_ROOT = Path(__file__).resolve().parents[2]
PROTOCOL_VERSION = "2025-06-18"
TIMEOUT = REPLY_TIMEOUT

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
    def __init__(
        self,
        command: Sequence[str] | None = None,
        env: Mapping[str, str] | None = None,
        *,
        cwd: Path | None = None,
        stderr: int = subprocess.PIPE,
    ) -> None:
        self.process = subprocess.Popen(
            list(command or server_command()),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=stderr,
            env=server_env(env),
            cwd=cwd or REPO_ROOT,
            start_new_session=True,  # its PID also identifies its workers after reparenting
        )
        self.stdout_lines: list[bytes] = []
        assert self.process.stdout is not None
        self._replies = ReplyStream(self.process.stdout, record=self.stdout_lines)
        # stderr is drained while the child runs: an unread pipe fills at 64 KiB and blocks the
        # child's writes, which looks like a child that never exits rather than a full pipe.
        self._stopping = threading.Event()
        self.stderr_chunks: list[bytes] = []
        self._stderr_reader: threading.Thread | None = None
        if self.process.stderr is not None:
            self._stderr_reader = threading.Thread(target=self._drain_stderr, daemon=True)
            self._stderr_reader.start()

    def _drain_stderr(self) -> None:
        assert self.process.stderr is not None
        self.stderr_chunks.extend(pipe_chunks(self.process.stderr, self._stopping))

    def send(self, message: Mapping[str, Any]) -> None:
        self.send_bytes(json.dumps(message).encode() + b"\n")
        ident = message.get("id")
        if isinstance(ident, (int, str)) and "method" in message:
            self._replies.track(ident)

    def send_bytes(self, data: bytes) -> None:
        assert self.process.stdin is not None
        self.process.stdin.write(data)
        self.process.stdin.flush()

    def read_line(self, timeout: float = TIMEOUT) -> bytes:
        try:
            return self._replies.read_line(timeout)[1]
        except queue.Empty:
            raise TimeoutError("child stdout readiness deadline expired") from None

    def receive(self, timeout: float = TIMEOUT) -> dict[str, Any]:
        return self._replies.receive(timeout)

    def receive_until(self, deadline: float) -> TimedReply:
        return self._replies.receive_until(deadline)

    def request(self, message: Mapping[str, Any], *, timeout: float = TIMEOUT) -> dict[str, Any]:
        """Send a request and return its response within one deadline, skipping notifications."""
        deadline = time.monotonic() + timeout
        self.send(message)
        (reply,) = self._replies.collect_until([message["id"]], deadline)
        return reply

    def initialize(self, *, timeout: float = TIMEOUT) -> dict[str, Any]:
        reply = self.request(INITIALIZE, timeout=timeout)
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
        self._finish_output()
        return returncode, self._stderr()

    def _stderr(self) -> str:
        return b"".join(self.stderr_chunks).decode(errors="replace")

    def _finish_output(self) -> None:
        # The direct child has exited. Drain its buffered output even if a descendant still holds the pipes open.
        self._stopping.set()
        self._replies.close()
        if self._stderr_reader is not None:
            self._stderr_reader.join(timeout=1)

    def __enter__(self) -> Self:
        return self

    def __exit__(self, _type: object, error: BaseException | None, _traceback: object) -> None:
        if self.process.poll() is None:
            self.process.kill()
            self.process.wait()
        self._finish_output()
        with suppress(BrokenPipeError):
            self.close_stdin()
        for stream in (self.process.stdout, self.process.stderr):
            if stream is not None:
                stream.close()
        if error is not None and self.stderr_chunks:
            error.add_note(f"Child stderr:\n{self._stderr()}")
