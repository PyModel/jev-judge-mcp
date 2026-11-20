"""Confirm a freshly written server answers initialize and tools/list. No provider call."""

import json
import os
import selectors
import signal
import subprocess
import time
from collections import deque
from collections.abc import Mapping, Sequence
from contextlib import suppress
from typing import IO, cast

from jev_judge_mcp.domain import decode_json, is_json_object
from jev_judge_mcp.install.launch import SERVER_NAME
from jev_judge_mcp.install.redact import redact_diagnostic
from jev_judge_mcp.registry import TOOL_NAMES

STDERR_TAIL_LINES = 5
"""How many of the child's last stderr lines a VerifyError carries (ADR-0053)."""

STDERR_LINE_LIMIT = 200
"""Per-line byte cap, so one enormous stderr line cannot flood the installer's summary."""


class VerifyError(Exception):
    """The server did not answer the install check. The message has no secrets."""


STDERR_CAPTURE_BYTES = 16 * 1024
STDOUT_LINE_BYTES = 1024 * 1024
STDOUT_TOTAL_BYTES = 4 * STDOUT_LINE_BYTES
STDOUT_MESSAGES = 256
_READ_SIZE = 8192


class _ClosedPipe(VerifyError):
    """The child closed a handshake pipe and may still be writing its final diagnostic."""


class _OutputLimit(VerifyError):
    pass


class _Output:
    """Read both pipes on demand. At most one bounded line and one read chunk are pending."""

    def __init__(self, selector: selectors.BaseSelector, stdout: IO[bytes], stderr: IO[bytes]) -> None:
        self.selector = selector
        selector.register(stdout, selectors.EVENT_READ, "stdout")
        selector.register(stderr, selectors.EVENT_READ, "stderr")
        self.stdout = bytearray()
        self.total = 0
        self.messages = 0
        self.eof = False
        self.stderr = bytearray()
        self.tail: deque[str] = deque(maxlen=STDERR_TAIL_LINES)

    def _stderr(self, chunk: bytes) -> None:
        parts = chunk.split(b"\n")
        for i, part in enumerate(parts):
            self.stderr.extend(part[: STDERR_CAPTURE_BYTES + 1 - len(self.stderr)])
            if i < len(parts) - 1:
                self._save_stderr_line()

    def _save_stderr_line(self) -> None:
        if len(self.stderr) > STDERR_CAPTURE_BYTES:
            self.tail.append("[oversized stderr line omitted]")
        else:
            self.tail.append(self.stderr.decode("utf-8", errors="replace").rstrip("\r"))
        self.stderr.clear()

    def read(self, timeout: float, *, finishing: bool = False) -> None:
        for key, _events in self.selector.select(timeout):
            data = os.read(key.fd, _READ_SIZE)
            if not data:
                self.selector.unregister(key.fd)
            if key.data == "stderr":
                self._stderr(data)
            elif not data:
                self.eof = True
            elif not finishing:
                self.total += len(data)
                if self.total > STDOUT_TOTAL_BYTES:
                    raise _OutputLimit("server stdout exceeded the total byte limit")
                self.stdout.extend(data)

    def line(self, deadline: float) -> bytes | None:
        offset = 0
        while (remaining := deadline - time.monotonic()) > 0:
            end = self.stdout.find(b"\n", offset)
            length = len(self.stdout) if end == -1 else end
            if length > STDOUT_LINE_BYTES:
                raise _OutputLimit("server stdout exceeded the line byte limit")
            if end >= 0 or (self.eof and self.stdout):
                self.messages += 1
                if self.messages > STDOUT_MESSAGES:
                    raise _OutputLimit("server stdout exceeded the message limit")
                result = bytes(self.stdout[:length])
                del self.stdout[: length + 1]
                return result
            if self.eof:
                return None
            offset = length
            self.read(remaining)
        raise VerifyError("timed out waiting for the server")

    def drain(self, timeout: float) -> None:
        deadline = time.monotonic() + timeout
        while self.selector.get_map() and time.monotonic() < deadline:
            self.read(max(0, deadline - time.monotonic()), finishing=True)

    def finish(self, secrets: Sequence[str]) -> str:
        # Read remaining pipe bytes after reaping, even if a descendant kept a pipe open.
        self.drain(0.2)
        if self.stderr:
            self._save_stderr_line()
        safe = redact_diagnostic("\n".join(self.tail), secrets) if any(self.tail) else ""
        lines = ["  " + line.encode()[:STDERR_LINE_LIMIT].decode(errors="ignore") for line in safe.splitlines() if line]
        return "the child's stderr, last lines:\n" + "\n".join(lines) if lines else ""


def verify_command(command: list[str], *, timeout: float = 30.0, secrets: Sequence[str] = ()) -> None:
    """Speak MCP over stdio, with one deadline for the handshake."""
    with (
        selectors.DefaultSelector() as selector,
        subprocess.Popen(  # noqa: S603
            command,
            start_new_session=True,
            bufsize=0,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        ) as process,
    ):
        assert process.stdin is not None and process.stdout is not None and process.stderr is not None
        output: _Output | None = None
        error: VerifyError | None = None
        completed = False
        try:
            output = _Output(selector, process.stdout, process.stderr)
            _handshake(process.stdin, output, time.monotonic() + timeout)
            completed = True
        except BrokenPipeError:
            error = _ClosedPipe("server closed stdin")
        except VerifyError as exc:
            error = exc
        finally:
            try:
                try:
                    process.stdin.close()
                except BrokenPipeError:
                    error = error or _ClosedPipe("server closed stdin")
                if isinstance(error, _ClosedPipe) and output is not None:
                    # A resolver may close its protocol pipe before it finishes writing stderr.
                    output.drain(0.5)
                elif completed:
                    with suppress(subprocess.TimeoutExpired):
                        process.wait(timeout=1)
            finally:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                except PermissionError:
                    # Darwin can return EPERM for an exited process group.
                    process.kill()
                process.wait()
        if error:
            suffix = output.finish(secrets) if output is not None and not isinstance(error, _OutputLimit) else ""
            raise VerifyError(f"{error}; {suffix}" if suffix else str(error))


def _handshake(stdin: IO[bytes], output: _Output, deadline: float) -> None:
    _send(stdin, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": _initialize_params()})
    initialized = _read_response(output, 1, deadline)
    info = initialized.get("serverInfo")
    if not is_json_object(info) or info.get("name") != SERVER_NAME:
        raise VerifyError("serverInfo.name is not jev-mcp")
    _send(stdin, {"jsonrpc": "2.0", "method": "notifications/initialized"})
    _send(stdin, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    listed = _read_response(output, 2, deadline)
    tools = listed.get("tools")
    if not isinstance(tools, list):
        raise VerifyError("tools/list did not return tools")
    names: set[str] = set()
    for item in cast(list[object], tools):
        if not is_json_object(item) or not isinstance(name := item.get("name"), str):
            raise VerifyError("tools/list returned an invalid tool name")
        names.add(name)
    missing = [tool for tool in TOOL_NAMES if tool not in names]
    if missing:
        raise VerifyError("tools/list is missing " + ", ".join(missing))


def _initialize_params() -> dict[str, object]:
    return {
        "protocolVersion": "2024-11-05",
        "capabilities": {},
        "clientInfo": {"name": "jev-judge-mcp-install", "version": "0"},
    }


def _send(stdin: IO[bytes], message: Mapping[str, object]) -> None:
    stdin.write((json.dumps(message) + "\n").encode())
    stdin.flush()


def _read_response(reader: _Output, request_id: int, deadline: float) -> dict[str, object]:
    # Only a response envelope can complete a request. Diagnostics exclude its body.
    while time.monotonic() < deadline:
        line = reader.line(deadline)
        if line is None:
            raise _ClosedPipe("server closed stdout")
        try:
            parsed = decode_json(line.decode("utf-8-sig"))
        except ValueError:
            continue
        if (
            not is_json_object(parsed)
            or parsed.get("jsonrpc") != "2.0"
            or "method" in parsed
            or ("result" in parsed) == ("error" in parsed)
            or type(parsed.get("id")) is not int
            or parsed["id"] != request_id
        ):
            continue
        if "error" in parsed:
            raise VerifyError("server returned an error")
        result = parsed.get("result")
        if not is_json_object(result):
            raise VerifyError("server reply has no result")
        return result
    raise VerifyError("timed out waiting for the server")
