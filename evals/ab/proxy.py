"""Stdio timing proxy: `python -m evals.ab.proxy LOG -- CMD [ARGS...]`.

Relays newline-delimited JSON-RPC between the MCP client and the server it spawns, unchanged
(`evals.relay`), and appends one JSONL row per `tools/call` to LOG: tool name, round-trip
milliseconds, isError, and the `usage.input_tokens`, `action`, and `model` fields of the tool result.
A call the server never answered is logged `unanswered` and errored when the server's stdout ends;
a request id a client reused logs the displaced call as `duplicate_id` before it is overwritten.
Arguments, result text and error messages are never logged: this recorder builds every row from the
tool name, timings and `result_fields` alone. The round trip includes the server's local work, so it
bounds provider latency from above. The server's stderr goes to LOG with a `.stderr` suffix.
"""

import json
import threading
import time
from collections.abc import Sequence
from typing import IO, Any, cast

from evals.relay import is_tools_call, pending_key, result_fields, serve


class Recorder:
    def __init__(self, log: IO[str]) -> None:
        self.log = log
        self.pending: dict[str, tuple[str, float]] = {}
        self.lock = threading.Lock()

    def request(self, message: object) -> None:
        if not (is_tools_call(message) and "id" in cast(dict[str, Any], message)):
            return
        call = cast(dict[str, Any], message)
        params = call.get("params")
        if not isinstance(params, dict):
            # Positional params (JSON-RPC permits an array) carry no `name` to record; forward only.
            return
        name = str(cast(dict[str, Any], params).get("name"))
        with self.lock:
            key = pending_key(call["id"])
            previous = self.pending.get(key)
            self.pending[key] = (name, time.perf_counter())
        if previous is not None:
            # A client reusing a request id would silently drop the displaced call's row; log it.
            self._write(previous[0], previous[1], {"is_error": True, "duplicate_id": True})

    def response(self, message: dict[str, Any]) -> None:
        # A raise while building the row must leave the call pending, so close() can still log it.
        key = pending_key(message["id"])
        with self.lock:
            if key not in self.pending:
                return
        fields = result_fields(message)
        with self.lock:
            started = self.pending.pop(key, None)
        if started is not None:
            try:
                self._write(*started, fields)
            except Exception:
                with self.lock:
                    self.pending.setdefault(key, started)
                raise

    def close(self) -> None:
        with self.lock:
            unanswered, self.pending = list(self.pending.values()), {}
        for tool, t0 in unanswered:
            self._write(tool, t0, {"is_error": True, "unanswered": True})

    def _write(self, tool: str, t0: float, fields: dict[str, Any]) -> None:
        row = {"tool": tool, "ms": round((time.perf_counter() - t0) * 1000, 1), **fields}
        with self.lock:
            self.log.write(json.dumps(row) + "\n")
            self.log.flush()


def main(argv: Sequence[str] | None = None) -> int:
    return serve(argv, "evals.ab.proxy", Recorder)


if __name__ == "__main__":
    raise SystemExit(main())
