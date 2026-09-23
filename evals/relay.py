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
- one locked writer serves the client's stdout, shared by relayed lines and the recorder's own replies.

What a row holds is the recorder's business: `evals.ab.proxy` logs no arguments or text,
`evals.bench.proxy` logs both and caps the calls it forwards.
"""

import json
import subprocess
import sys
import threading
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
        self.lock = threading.Lock()
        self.aborted = False
        self.calls: dict[str, tuple[str | None, float, float]] = {}

    def begin(self) -> bool:
        """True for the first caller to abort."""
        with self.lock:
            first = not self.aborted
            self.aborted = True
            return first

    def note(self, message: object) -> None:
        if not isinstance(message, dict):
            return
        call = cast(dict[str, Any], message)
        if not is_tools_call(call) or "id" not in call:
            return
        key = pending_key(call["id"])
        with self.lock:
            if self.aborted or key in self.calls:
                return
            self.calls[key] = (_tool_name(call), time.time(), time.perf_counter())

    def drop(self, message_id: object) -> None:
        with self.lock:
            self.calls.pop(pending_key(message_id), None)

    def snapshot(self) -> list[tuple[str | None, float, float]]:
        with self.lock:
            return list(self.calls.values())


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


class _Client:
    """The client's stdout: one writer for relayed lines and recorder replies, silent once closed."""

    def __init__(self, sink: IO[bytes]) -> None:
        self.sink = sink
        self.lock = threading.Lock()
        self.closed = False

    def write(self, line: bytes) -> None:
        with self.lock:
            if not self.closed:
                self.sink.write(line)
                self.sink.flush()

    def close(self) -> None:
        with self.lock:
            self.closed = True
            self.sink.close()


def _upstream(
    source: IO[bytes],
    server: IO[bytes],
    client: _Client,
    recorder: Recorder,
    pending: _Pending,
    abort: Callable[..., int],
) -> None:
    try:
        for line in iter(source.readline, b""):
            message = decode(line)
            try:
                reply = recorder.request(message)
            except Exception:
                pending.note(message)
                abort(close_recorder=True)
                return
            if reply is not None:
                client.write(reply)
                continue
            pending.note(message)
            server.write(line)
            server.flush()
        server.close()
    except BrokenPipeError:
        return
    except Exception:
        abort(close_recorder=True)


def serve(argv: Sequence[str] | None, prog: str, recorder_factory: Callable[[IO[str]], Recorder]) -> int:
    """Run `python -m PROG LOG -- CMD [ARGS...]`; returns the server's exit code."""
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) < 3 or args[1] != "--":
        sys.stderr.write(f"usage: python -m {prog} LOG -- CMD [ARGS...]\n")
        return 2
    log_path = Path(args[0])
    # A private reader on fd 0: the upstream thread may still block in it at exit, and a daemon thread
    # holding `sys.stdin.buffer`'s lock aborts interpreter shutdown.
    source = open(sys.stdin.fileno(), "rb", closefd=False)
    with log_path.open("a", encoding="utf-8") as log, log_path.with_suffix(".stderr").open("ab") as err:
        recorder = recorder_factory(log)
        client = _Client(sys.stdout.buffer)
        pending = _Pending()
        child = subprocess.Popen(args[2:], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=err)
        assert child.stdin is not None and child.stdout is not None

        def read_log() -> str:
            log.flush()
            return log_path.read_text(encoding="utf-8") if log_path.exists() else ""

        def write_fallback(before: str) -> None:
            try:
                after = read_log()
            except OSError:
                after = before
            for row in _fallback_rows(pending.snapshot(), before, after):
                log.write(json.dumps(row) + "\n")
            log.flush()

        def abort(*, close_recorder: bool, before: str | None = None) -> int:
            if pending.begin():
                try:
                    traceback.print_exc()
                    sys.stderr.flush()
                    if before is None:
                        try:
                            before = read_log()
                        except OSError:
                            before = ""
                    if close_recorder:
                        try:
                            recorder.close()
                        except Exception:
                            traceback.print_exc()
                            sys.stderr.flush()
                            write_fallback(before)
                    else:
                        write_fallback(before)
                    client.close()
                finally:
                    child.terminate()
            try:
                return child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
                return child.wait()

        threading.Thread(
            target=_upstream, args=(source, child.stdin, client, recorder, pending, abort), daemon=True
        ).start()
        try:
            for line in iter(child.stdout.readline, b""):
                message = decode(line)
                if isinstance(message, dict) and "id" in message and "method" not in message:
                    body = cast(dict[str, Any], message)
                    try:
                        recorder.response(body)
                    except Exception:
                        return abort(close_recorder=True)
                    pending.drop(body["id"])
                client.write(line)
            if pending.aborted:
                return abort(close_recorder=False)
            try:
                before_close = read_log()
            except OSError:
                before_close = ""
            try:
                recorder.close()
            except Exception:
                return abort(close_recorder=False, before=before_close)
        except Exception:
            return abort(close_recorder=True)
        client.close()
        return child.wait()
