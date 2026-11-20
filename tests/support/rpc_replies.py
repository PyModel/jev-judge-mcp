"""Collect correlated JSON-RPC replies from fixture subprocesses, including partial or stalled pipes."""

import json
import os
import queue
import selectors
import threading
import time
from collections import deque
from collections.abc import Iterator, Sequence
from typing import IO, Any, cast

REPLY_TIMEOUT = 15.0
TimedReply = tuple[float, dict[str, Any]]


def pipe_chunks(pipe: IO[bytes], stopping: threading.Event) -> Iterator[bytes]:
    """Let teardown cancel a read even when a descendant inherited the write end."""
    with selectors.DefaultSelector() as selector:
        selector.register(pipe, selectors.EVENT_READ)
        while not stopping.is_set():
            if selector.select(timeout=0.1):
                chunk = os.read(pipe.fileno(), 65536)
                if not chunk:
                    return
                yield chunk


class ReplyStream:
    def __init__(self, pipe: IO[bytes], *, record: list[bytes] | None = None) -> None:
        self._lines: queue.Queue[tuple[float, bytes] | OSError | None] = queue.Queue()
        self._seen: set[int | str] = set()
        self._outstanding: set[int | str] = set()
        self._deferred: deque[TimedReply] = deque()
        self._stopping = threading.Event()

        def emit(line: bytes) -> None:
            arrived = time.monotonic()
            if record is not None:
                record.append(line)
            self._lines.put((arrived, line))

        def read() -> None:
            partial = bytearray()
            try:
                for chunk in pipe_chunks(pipe, self._stopping):
                    *lines, tail = chunk.split(b"\n")
                    for line in lines:
                        partial.extend(line)
                        emit(bytes(partial) + b"\n")
                        partial.clear()
                    partial.extend(tail)
                if partial:
                    emit(bytes(partial))
            except OSError as error:
                self._lines.put(error)
            finally:
                self._lines.put(None)

        self.reader = threading.Thread(target=read, daemon=True)
        self.reader.start()

    def close(self) -> None:
        self._stopping.set()
        self.reader.join(timeout=1)

    def track(self, ident: int | str) -> None:
        self._outstanding.add(ident)

    def receive(self, timeout: float) -> dict[str, Any]:
        return self._receive(timeout)[1]

    def receive_until(self, deadline: float) -> TimedReply:
        """Include deferred replies, retaining the reader's original observation time."""
        return self._read_until(deadline, include_deferred=True)

    def _receive(self, timeout: float) -> TimedReply:
        if self._deferred:
            return self._deferred.popleft()
        arrived, message = self._read(timeout)
        ident = message.get("id")
        if isinstance(ident, (int, str)) and ident in self._outstanding:
            self._outstanding.remove(ident)
            self._seen.add(ident)
        return arrived, message

    def _read(self, timeout: float) -> TimedReply:
        entry = self._lines.get(timeout=timeout)
        if entry is None:
            self._lines.put(None)
            raise EOFError("server stdout reached EOF")
        if isinstance(entry, OSError):
            raise entry
        arrived, line = entry
        if not line.endswith(b"\n"):
            raise ValueError("server stdout reached EOF during a JSON-RPC frame")
        message: object = json.loads(line)
        if not isinstance(message, dict):
            raise ValueError("expected a JSON-RPC object")
        return arrived, cast(dict[str, Any], message)

    def _read_until(self, deadline: float, *, include_deferred: bool = False) -> TimedReply:
        remaining = max(0.0, deadline - time.monotonic())
        try:
            frame = self._receive(remaining) if include_deferred else self._read(remaining)
        except queue.Empty:
            raise TimeoutError("server reply deadline expired") from None
        if frame[0] > deadline:
            raise TimeoutError("server reply arrived after deadline")
        return frame

    def _response(self, deadline: float) -> tuple[int | str, TimedReply]:
        while True:
            frame = self._read_until(deadline)
            _, message = frame
            if message.get("jsonrpc") != "2.0":
                raise ValueError("expected JSON-RPC 2.0")
            if "method" in message:
                if (
                    "id" in message
                    or not isinstance(message["method"], str)
                    or "result" in message
                    or "error" in message
                ):
                    raise ValueError("unexpected server request or invalid notification")
                continue
            ident = message.get("id")
            if (
                isinstance(ident, bool)
                or not isinstance(ident, (int, str))
                or ("result" in message) == ("error" in message)
            ):
                raise ValueError("invalid JSON-RPC reply id or payload")
            return ident, frame

    def _unexpected(self, ident: int | str) -> ValueError:
        reason = "duplicate" if ident in self._seen else "unknown"
        detail = f" {ident}" if isinstance(ident, int) else " (string)"
        return ValueError(f"{reason} reply id{detail}")

    def collect(self, ids: Sequence[int | str], timeout: float = REPLY_TIMEOUT) -> list[dict[str, Any]]:
        return self.collect_until(ids, time.monotonic() + timeout)

    def collect_until(self, ids: Sequence[int | str], deadline: float) -> list[dict[str, Any]]:
        pending = set(ids)
        replies: dict[int | str, dict[str, Any]] = {}
        while pending:
            try:
                ident, frame = self._response(deadline)
            except (EOFError, TimeoutError) as error:
                missing = [ident for ident in ids if ident in pending]
                raise type(error)(f"{error}, missing reply ids {missing}") from error
            if ident in self._seen or (ident not in pending and ident not in self._outstanding):
                raise self._unexpected(ident)
            self._outstanding.discard(ident)
            self._seen.add(ident)
            if ident in pending:
                pending.remove(ident)
                replies[ident] = frame[1]
            else:
                self._deferred.append(frame)
        return [replies[ident] for ident in ids]

    def expect_eof(self, timeout: float = REPLY_TIMEOUT) -> None:
        """After closing stdin, reject any surplus reply, including duplicates after the last batch."""
        try:
            ident, _ = self._response(time.monotonic() + timeout)
        except EOFError:
            return
        raise self._unexpected(ident)
