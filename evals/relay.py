"""The recording stdio relay both paid proxies run: `serve(argv, prog, recorder_factory)`.

Relays newline-delimited JSON-RPC between the MCP client and the server it spawns (`LOG -- CMD
[ARGS...]`) and shows each message to a `Recorder`, which logs to LOG and may answer a request itself
instead of forwarding it. Server stderr goes to LOG with a `.stderr` suffix.

The relay owns what both recorders need to agree on (ADR-0026):
- only a message with an `id` and no `method` is handed over as a response, so a server-to-client
  request whose id collides with a pending call is relayed, never logged as that call's answer;
- at the server's EOF the recorder is closed, so every call still pending is logged as unanswered,
  and then the client's stdout is closed;
- when a recorder call raises, the traceback is logged, then `Recorder.close()` logs every call
  still pending in that recorder's own row shape; only if `close` itself raises does the relay write
  a fallback row (no arguments, no result text, `seq` above any seq already in the log). The client's
  stdout is closed, and only then is the child terminated;
- one nonblocking writer serves the client's stdout, shared by relayed lines and the recorder's own replies.

What a row holds is the recorder's business: `evals.ab.proxy` logs no arguments or text,
`evals.bench.proxy` logs both and caps the calls it forwards.
"""

import json
import os
import select
import stat
import subprocess
import sys
import time
import traceback
from collections import Counter
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import IO, Any, Protocol, cast


class Recorder(Protocol):
    def request(self, message: object) -> bytes | None:
        """Called with each decoded client line (None if it is not JSON). None forwards the line;
        bytes are sent to the client instead, and the line never reaches the server."""
        ...

    def response(self, message: dict[str, Any]) -> None:
        """Called with each server message that has an `id` and no `method`."""
        ...

    def close(self) -> None:
        """The server's stdout ended: log every call still pending as unanswered."""
        ...


def decode(line: bytes) -> object:
    try:
        return json.loads(line)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None


def is_tools_call(message: object) -> bool:
    return isinstance(message, dict) and cast(dict[str, Any], message).get("method") == "tools/call"


ACTIONS = ("auto", "review", "escalate")
"""Caller-facing Actions, least to most severe."""
HEADLINE_TOOLS: dict[str, tuple[str, ...]] = {
    "jev_review": ("action",),
    "jev_gate": ("action",),
    "jev_gate_range": ("action",),
    "jev_screen": ("recommendation", "action"),
    "jev_compare": ("overall", "decision"),
    "jev_verify": ("results", "action"),
    "jev_classify": ("results", "decision"),
    "jev_extract": ("results", "status"),
}
"""Where each tool's payload carries its Actions (ADR-0013's headline): a top-level key, a key of one
object, or a key of each item in a list. Screen counts only its `review`; compare's aspects do not count."""


def headline_action(body: dict[str, Any]) -> str | None:
    """The tool's one headline Action: the most severe Action it returned, or None when it returned none."""
    path = HEADLINE_TOOLS.get(str(body.get("tool")))
    if path is None:
        return None
    values: list[object] = [body.get(path[0])]
    if len(path) == 2:
        holder = body.get(path[0])
        items = cast(list[object], holder) if isinstance(holder, list) else [holder]
        values = [cast(dict[str, Any], item).get(path[1]) for item in items if isinstance(item, dict)]
    ranks = [ACTIONS.index(value) for value in values if value in ACTIONS]
    return ACTIONS[max(ranks)] if ranks else None


def result_fields(message: dict[str, Any]) -> dict[str, Any]:
    """isError and the result body's `usage.input_tokens`, `action`, headline Action and `model`; never text."""
    result = cast(dict[str, Any], message.get("result") or {})
    row: dict[str, Any] = {"is_error": bool(result.get("isError")) or "error" in message}
    text = result_text(message)
    try:
        payload = json.loads(text) if isinstance(text, str) else None
    except json.JSONDecodeError:
        payload = None
    if isinstance(payload, dict):
        body = cast(dict[str, Any], payload)
        usage = body.get("usage")
        row["input_tokens"] = cast(dict[str, Any], usage).get("input_tokens") if isinstance(usage, dict) else None
        row["action"] = body.get("action")
        row["headline_action"] = headline_action(body)
        row["model"] = body.get("model")
    return row


def result_text(message: dict[str, Any]) -> object:
    content = cast(list[dict[str, Any]], cast(dict[str, Any], message.get("result") or {}).get("content") or [])
    return content[0].get("text") if content and content[0].get("type") == "text" else None


def pending_key(message_id: object) -> str:
    return json.dumps(message_id)


def _tool_name(message: dict[str, Any]) -> str | None:
    params = message.get("params")
    name: object = None
    if isinstance(params, dict):
        name = cast(dict[str, Any], params).get("name")
    elif isinstance(params, list) and params:
        name = cast(list[object], params)[0]
    return None if name is None else str(name)


class _Pending:
    """Calls forwarded to the child and not yet logged. Used only when `Recorder.close` itself raises."""

    def __init__(self) -> None:
        self.calls: dict[str, tuple[str | None, float, float]] = {}

    def note(self, message: object) -> None:
        if isinstance(message, dict):
            call = cast(dict[str, Any], message)
            if is_tools_call(call) and "id" in call:
                self.calls[pending_key(call["id"])] = (_tool_name(call), time.time(), time.perf_counter())


def _rows_in(text: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            rows.append(cast(dict[str, Any], value))
    return rows


def _tool_key(tool: object) -> str:
    return str(tool) if tool is not None else ""


def _fallback_rows(calls: list[tuple[str | None, float, float]], before: str, after: str) -> list[dict[str, Any]]:
    """Relay rows for calls `close` did not already log, numbered above every seq in the log.

    seq, t0, t1, and ms are the fields the bench already indexes. Arguments and result text stay out.
    """
    fresh = _rows_in(after[len(before) :] if after.startswith(before) else after)
    seqs: list[int] = []
    for row in _rows_in(after):
        seq = row.get("seq")
        if isinstance(seq, int) and not isinstance(seq, bool):
            seqs.append(seq)
    next_seq = max(seqs, default=-1) + 1
    unanswered: Counter[str] = Counter(_tool_key(row.get("tool")) for row in fresh if row.get("unanswered"))
    now, perf = time.time(), time.perf_counter()
    rows: list[dict[str, Any]] = []
    for tool, t0, started in calls:
        key = _tool_key(tool)
        if unanswered[key] > 0:
            unanswered[key] -= 1
            continue
        rows.append(
            {
                "seq": next_seq,
                "tool": tool,
                "t0": t0,
                "t1": now,
                "ms": round((perf - started) * 1000, 1),
                "refused": False,
                "is_error": True,
                "unanswered": True,
                "recorder_error": True,
            }
        )
        next_seq += 1
    return rows


def private_opener(path: str, flags: int) -> int:
    """Open an operator-owned regular file, private from creation, validating before truncation."""
    fd = os.open(path, (flags & ~os.O_TRUNC) | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or info.st_uid != os.getuid() or info.st_nlink != 1:
            raise PermissionError("study trace must be a private, singly linked regular file")
        if flags & os.O_TRUNC:
            os.ftruncate(fd, 0)
    except BaseException:
        os.close(fd)
        raise
    return fd


class _Stream:
    """A line-oriented reader. Queue at most one large frame plus one read at a time."""

    def __init__(self, fd: int) -> None:
        self.fd = fd
        self.partial = bytearray()
        self.eof = False

    def lines(self) -> list[bytes]:
        chunk = os.read(self.fd, 8192)
        self.eof = not chunk
        parts = chunk.split(b"\n")
        self.partial.extend(parts[0])
        if len(self.partial) > 64 * 1024 * 1024:
            raise ValueError("relay frame exceeds 64 MiB")
        if len(parts) == 1:
            if self.eof and self.partial:
                line = bytes(self.partial)
                self.partial.clear()
                return [line]
            return []
        lines = [bytes(self.partial) + b"\n", *(part + b"\n" for part in parts[1:-1])]
        self.partial = bytearray(parts[-1])
        return lines


def _pump(child: subprocess.Popen[bytes], recorder: Recorder, pending: _Pending, idle_timeout: float) -> None:
    assert child.stdin is not None and child.stdout is not None
    source = _Stream(sys.stdin.fileno())
    replies = _Stream(child.stdout.fileno())
    server_fd = child.stdin.fileno()
    client_fd = sys.stdout.fileno()
    queued = {server_fd: bytearray(), client_fd: bytearray()}
    for fd in queued:
        os.set_blocking(fd, False)
    waiting: set[str] = set()
    while not replies.eof or queued[client_fd]:
        reads: list[int] = []
        if not replies.eof and len(queued[client_fd]) < 65536:
            reads.append(replies.fd)
            if not source.eof and len(queued[server_fd]) < 65536:
                reads.append(source.fd)
        writes = [fd for fd, data in queued.items() if data]
        active = writes or waiting or source.eof or source.partial or replies.partial
        readable, writable, _ = select.select(reads, writes, [], idle_timeout if active else None)
        if not readable and not writable:
            raise TimeoutError("no progress on active relay")
        for fd in writable:
            try:
                written = os.write(fd, queued[fd][:8192])
            except BlockingIOError:
                continue
            del queued[fd][:written]
        if source.fd in readable:
            for line in source.lines():
                message = decode(line)
                pending.note(message)
                reply = recorder.request(message)
                envelope = cast(dict[str, Any], message) if isinstance(message, dict) else {}
                if reply is None:
                    queued[server_fd].extend(line)
                    if "method" in envelope and "id" in envelope:
                        waiting.add(pending_key(envelope["id"]))
                else:
                    if "id" in envelope and pending_key(envelope["id"]) not in waiting:
                        pending.calls.pop(pending_key(envelope["id"]), None)
                    queued[client_fd].extend(reply)
        if source.eof and not queued[server_fd] and not child.stdin.closed:
            child.stdin.close()
        if replies.fd in readable:
            for line in replies.lines():
                message = decode(line)
                if isinstance(message, dict) and "id" in message and "method" not in message:
                    response = cast(dict[str, Any], message)
                    recorder.response(response)
                    key = pending_key(response["id"])
                    waiting.discard(key)
                    pending.calls.pop(key, None)
                queued[client_fd].extend(line)
            if replies.eof:
                queued[server_fd].clear()


def serve(
    argv: Sequence[str] | None, prog: str, recorder_factory: Callable[[IO[str]], Recorder], *, idle_timeout: float = 180
) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) < 3 or args[1] != "--":
        sys.stderr.write(f"usage: python -m {prog} LOG -- CMD [ARGS...]\n")
        return 2
    log_path = Path(args[0])
    with (
        open(log_path, "a+", encoding="utf-8", opener=private_opener) as log,
        open(log_path.with_suffix(".stderr"), "ab", opener=private_opener) as err,
    ):
        recorder = recorder_factory(log)
        pending = _Pending()
        child = subprocess.Popen(args[2:], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=err)
        failed = False
        try:
            try:
                _pump(child, recorder, pending, idle_timeout)
            except Exception:
                failed = True
                traceback.print_exc()
            log.flush()
            before = log.tell()
            try:
                recorder.close()
            except Exception:
                failed = True
                traceback.print_exc()
                log.seek(0)
                old = bytes(log.buffer.read(before)).decode("utf-8")
                log.seek(0)
                for row in _fallback_rows(list(pending.calls.values()), old, log.read()):
                    log.write(json.dumps(row) + "\n")
                log.flush()
            sys.stdout.close()
            if failed:
                child.terminate()
            try:
                code = child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
                return 1
            return 1 if failed else code
        finally:
            if child.poll() is None:
                child.kill()
                child.wait()
            if child.stdin:
                child.stdin.close()
            if child.stdout:
                child.stdout.close()
