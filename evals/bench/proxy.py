"""Bench stdio proxy: `python -m evals.bench.proxy LOG -- CMD [ARGS...]`.

The shared relay (`evals.relay`) with the bench's own recorder. Each `tools/call` row carries its
arrival order, epoch start/end timestamps next to the perf-counter round trip, and the arguments and
result text (a JSON-RPC error's message when there is no result): bench items are synthetic, and
Jev's own accuracy is scored from them. A call the server never answered is logged `unanswered` and
errored when the server's stdout ends. A run's calls beyond `BENCH_REQUEST_CAP` never reach the
server: the proxy answers them itself with an `isError` result and logs them `refused`. So does every
`tools/call` it could not pair or count: one without an id, one with positional params, or one
inside a batch array. A request id the client reused logs the displaced call as `duplicate_id`
before it is overwritten. Everything
else is relayed unchanged; server stderr goes to LOG with a `.stderr` suffix.
"""

import json
import threading
import time
from collections.abc import Sequence
from typing import IO, Any, cast

from evals.relay import is_tools_call, pending_key, result_fields, result_text, serve

BENCH_REQUEST_CAP = 25
"""Jev calls one bench run may send. The bench's own cap, not `make eval-live`'s `LIVE_REQUEST_CAP`;
25 full requests stay inside a run's Jev headroom, `evals.ab.ledger.JEV_RUN_BOUND_USD`."""
REFUSAL = f"bench cap: this run already made {BENCH_REQUEST_CAP} Jev calls; no more are sent"
NO_ID_REFUSAL = "bench proxy: a tools/call without an id is not sent"
BATCH_REFUSAL = "bench proxy: a JSON-RPC batch with a tools/call is not sent"
PARAMS_REFUSAL = "bench proxy: a tools/call with positional params is not sent"
INVALID_REQUEST = -32600


def _reply(message_id: Any, body: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": message_id, **body}


def _refusal(text: str) -> dict[str, Any]:
    return {"result": {"content": [{"type": "text", "text": text}], "isError": True}}


def _line(value: Any) -> bytes:
    return (json.dumps(value) + "\n").encode()


class BenchRecorder:
    def __init__(self, log: IO[str], cap: int = BENCH_REQUEST_CAP) -> None:
        self.log = log
        self.cap = cap
        self.seq = 0
        self.forwarded = 0
        self.pending: dict[str, dict[str, Any]] = {}
        self.lock = threading.Lock()

    def request(self, message: object) -> bytes | None:
        """None to forward the line; otherwise what the proxy sends instead (empty for nothing)."""
        if isinstance(message, list):
            batch = cast(list[Any], message)
            return self._refuse_batch(batch) if any(is_tools_call(item) for item in batch) else None
        if not is_tools_call(message):
            return None
        call = cast(dict[str, Any], message)
        with self.lock:
            if not isinstance(call.get("params"), dict):
                # Positional params (JSON-RPC permits an array) cannot be rowed or counted.
                self._refuse(call, PARAMS_REFUSAL)
                return _line(_reply(call["id"], _refusal(PARAMS_REFUSAL))) if "id" in call else b""
            if "id" not in call:
                self._refuse(call, NO_ID_REFUSAL)
                return b""
            if self.forwarded >= self.cap:
                self._refuse(call, REFUSAL)
                return _line(_reply(call["id"], _refusal(REFUSAL)))
            self.forwarded += 1
            key = pending_key(call["id"])
            previous = self.pending.get(key)
            if previous is not None:
                # A reused request id would silently drop the displaced call's row; log it.
                self._write(
                    self._finish(previous, {"refused": False, "is_error": True, "duplicate_id": True, "text": None})
                )
            self.pending[key] = {**self._row(call), "perf": time.perf_counter()}
        return None

    def response(self, message: dict[str, Any]) -> None:
        # A raise while building the row must leave the call pending, so close() can still log it.
        key = pending_key(message["id"])
        with self.lock:
            if key not in self.pending:
                return
        text = result_text(message)
        error = message.get("error")
        if text is None and isinstance(error, dict):
            text = cast(dict[str, Any], error).get("message")
        fields = {"refused": False, **result_fields(message), "text": text}
        with self.lock:
            started = self.pending.pop(key, None)
        if started is None:
            return
        try:
            with self.lock:
                self._write(self._finish(started, fields))
        except Exception:
            with self.lock:
                self.pending.setdefault(key, started)
            raise

    def close(self) -> None:
        with self.lock:
            unanswered, self.pending = list(self.pending.values()), {}
            for started in unanswered:
                fields = {"refused": False, "is_error": True, "unanswered": True, "text": None}
                self._write(self._finish(started, fields))

    def _refuse_batch(self, batch: list[Any]) -> bytes:
        replies: list[dict[str, Any]] = []
        with self.lock:
            for item in batch:
                if not isinstance(item, dict):
                    continue
                entry = cast(dict[str, Any], item)
                call = is_tools_call(entry)
                if call:
                    self._refuse(entry, BATCH_REFUSAL)
                if "id" in entry:
                    error = {"error": {"code": INVALID_REQUEST, "message": BATCH_REFUSAL}}
                    replies.append(_reply(entry["id"], _refusal(BATCH_REFUSAL) if call else error))
        return _line(replies) if replies else b""

    def _row(self, call: dict[str, Any]) -> dict[str, Any]:
        """A new row in arrival order; the caller holds the lock. Positional params row as no tool."""
        params: object = call.get("params")
        if not isinstance(params, dict):
            params = {}
        fields = cast(dict[str, Any], params)
        row = {
            "seq": self.seq,
            "tool": str(fields.get("name")),
            "t0": time.time(),
            "arguments": fields.get("arguments"),
        }
        self.seq += 1
        return row

    def _refuse(self, call: dict[str, Any], text: str) -> None:
        row = self._row(call)
        self._write({**row, "t1": row["t0"], "ms": 0.0, "is_error": True, "refused": True, "text": text})

    @staticmethod
    def _finish(started: dict[str, Any], fields: dict[str, Any]) -> dict[str, Any]:
        row = dict(started)
        ms = round((time.perf_counter() - row.pop("perf")) * 1000, 1)
        return {**row, "t1": time.time(), "ms": ms, **fields}

    def _write(self, row: dict[str, Any]) -> None:
        self.log.write(json.dumps(row) + "\n")
        self.log.flush()


def main(argv: Sequence[str] | None = None) -> int:
    return serve(argv, "evals.bench.proxy", BenchRecorder)


if __name__ == "__main__":
    raise SystemExit(main())
