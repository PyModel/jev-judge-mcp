"""Confirm a freshly written server answers initialize and tools/list. No provider call."""

import json
import select
import subprocess
import threading
import time
from collections import deque
from collections.abc import Mapping
from typing import IO, cast

from jev_judge_mcp.domain import is_json_object
from jev_judge_mcp.install.launch import SERVER_NAME

EXPECTED_TOOLS: tuple[str, ...] = (
    "jev_verify",
    "jev_screen",
    "jev_find",
    "jev_classify",
    "jev_decide",
    "jev_rerank",
    "jev_compare",
    "jev_extract",
    "jev_review",
    "jev_gate",
    "jev_score",
)
"""The published tool names: the reference ten in snapshot order, then the extension (ADR-0048)."""

STDERR_TAIL_LINES = 5
"""How many of the child's last stderr lines a VerifyError carries (ADR-0053)."""

STDERR_LINE_LIMIT = 200
"""Per-line character cap, so one enormous stderr line cannot flood the installer's summary."""


class VerifyError(Exception):
    """The server did not answer the install check. The message has no secrets."""


class _StderrTail:
    """The child's last stderr lines, drained while stdout is read.

    The pipe is drained from a thread, so a chatty child cannot fill it and stall the handshake
    until the timeout (ADR-0053): the child keeps writing, the thread keeps reading, and only the
    short tail is kept for the error message.
    """

    def __init__(self, stream: IO[str] | None) -> None:
        self._lines: deque[str] = deque(maxlen=STDERR_TAIL_LINES)
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._drain, args=(stream,), daemon=True)

    def start(self) -> None:
        self._thread.start()

    def join(self, timeout: float) -> None:
        self._thread.join(timeout)

    def _drain(self, stream: IO[str] | None) -> None:
        if stream is None:
            return
        try:
            for line in stream:
                text = line.rstrip("\r\n")[:STDERR_LINE_LIMIT]
                with self._lock:
                    self._lines.append(text)
        except (OSError, ValueError):
            pass  # the pipe closed under us during teardown; the tail so far is enough

    def suffix(self) -> str:
        """The tail as an error-message suffix, or an empty string when the child said nothing."""
        with self._lock:
            kept = [line for line in self._lines if line]
        if not kept:
            return ""
        return "the child's stderr, last lines:\n" + "\n".join(f"  {line}" for line in kept)


def verify_command(command: list[str], *, timeout: float = 30.0) -> None:
    """Speak MCP over stdio. Stdin stays open until both replies arrive."""
    # The command list is the absolute uvx path plus fixed arguments, not a shell string.
    process = subprocess.Popen(  # noqa: S603
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    tail = _StderrTail(process.stderr)
    tail.start()
    try:
        stdin = process.stdin
        stdout = process.stdout
        if stdin is None or stdout is None:
            raise VerifyError("could not talk to the server")
        _send(stdin, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": _initialize_params()})
        initialized = _read_response(stdout, 1, timeout)
        info = initialized.get("serverInfo")
        name = info.get("name") if is_json_object(info) else None
        if name != SERVER_NAME:
            raise VerifyError("serverInfo.name is not jev-mcp")
        _send(stdin, {"jsonrpc": "2.0", "method": "notifications/initialized"})
        _send(stdin, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        listed = _read_response(stdout, 2, timeout)
        tools = listed.get("tools")
        if not isinstance(tools, list):
            raise VerifyError("tools/list did not return tools")
        names = {item.get("name") for item in cast(list[object], tools) if is_json_object(item)}
        missing = [tool for tool in EXPECTED_TOOLS if tool not in names]
        if missing:
            raise VerifyError("tools/list is missing " + ", ".join(missing))
        stdin.close()
    except VerifyError as exc:
        # The child's own words (uv's resolver, a crash) are the actionable part (ADR-0053). The
        # installer redacts this message with the same pass as every other summary line.
        first = exc.args[0] if exc.args else "the check failed"
        suffix = tail.suffix()
        raise VerifyError(f"{first}; {suffix}" if suffix else first) from None
    finally:
        if process.stdin is not None and not process.stdin.closed:
            process.stdin.close()
        process.wait(timeout=5)
        tail.join(timeout=1)
        if process.stdout is not None:
            process.stdout.close()
        if process.stderr is not None:
            process.stderr.close()


def _initialize_params() -> dict[str, object]:
    return {
        "protocolVersion": "2024-11-05",
        "capabilities": {},
        "clientInfo": {"name": "jev-judge-mcp-install", "version": "0"},
    }


def _send(stdin: IO[str], message: Mapping[str, object]) -> None:
    stdin.write(json.dumps(message) + "\n")
    stdin.flush()


def _read_response(stdout: IO[str], request_id: int, timeout: float) -> dict[str, object]:
    # A line that is not our reply (a notification, a log) is ignored until the id matches.
    # The body is never returned to the caller, so a secret in it cannot reach the summary.
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        ready, _, _ = select.select([stdout], [], [], deadline - time.monotonic())
        if not ready:
            raise VerifyError("timed out waiting for the server")
        line = stdout.readline()
        if line == "":
            raise VerifyError("server closed stdout")
        try:
            parsed: object = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not is_json_object(parsed) or parsed.get("id") != request_id:
            continue
        if parsed.get("error") is not None:
            raise VerifyError("server returned an error")
        result = parsed.get("result")
        if not is_json_object(result):
            raise VerifyError("server reply has no result")
        return result
    raise VerifyError("timed out waiting for the server")
