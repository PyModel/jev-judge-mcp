"""Private TypeSafe request journal shared by the broker and the study."""

import json
from dataclasses import dataclass
from threading import Lock
from typing import cast

RECEIPT_FILE = "broker-receipts.jsonl"
MAX_RECEIPT_BYTES = 4 * 1024 * 1024
MAX_REQUESTS = MAX_RECEIPT_BYTES // 200


def input_tokens(value: object) -> int | None:
    return value if type(value) is int and 0 <= value < 2**63 else None


def response_tokens(raw: bytes) -> int | None:
    try:
        data: object = json.loads(raw)
    except (ValueError, RecursionError):
        return None
    usage = cast(dict[str, object], data).get("usage") if isinstance(data, dict) else None
    return input_tokens(cast(dict[str, object], usage).get("input_tokens")) if isinstance(usage, dict) else None


class Journal:
    def __init__(self, path: str) -> None:
        self.path = path
        self.lock = Lock()
        self.started = 0
        self.sealed = False
        self._append(event="open")

    def _append(self, **row: str | int | None) -> None:
        with open(self.path, "a", encoding="ascii") as handle:
            handle.write(json.dumps(row, separators=(",", ":")) + "\n")

    def start(self) -> int:
        with self.lock:
            if self.sealed or self.started >= MAX_REQUESTS:
                raise OSError("TypeSafe receipt journal closed or full")
            ticket = self.started + 1
            self._append(event="start", id=ticket)
            self.started = ticket
            return ticket

    def settle(self, ticket: int, tokens: int | None) -> None:
        with self.lock:
            if not self.sealed:
                self._append(event="settle", id=ticket, input_tokens=tokens)

    def seal(self) -> None:
        with self.lock:
            self.sealed = True
            self._append(event="seal", started=self.started)


@dataclass(frozen=True)
class ReceiptUsage:
    tokens: tuple[int, ...]
    issue: str | None


def read_usage(raw: bytes) -> ReceiptUsage:
    if not raw:
        return ReceiptUsage((), "broker receipts missing")
    lines = raw.splitlines()
    started = 0
    settled: dict[int, int | None] = {}
    sealed = False
    issue = None
    try:
        if json.loads(lines[0]) != {"event": "open"}:
            raise ValueError("opening receipt")
        for line in lines[1:]:
            if sealed:
                raise ValueError("receipt after seal")
            loaded: object = json.loads(line)
            if not isinstance(loaded, dict):
                raise ValueError("receipt object")
            row = cast(dict[str, object], loaded)
            ticket = row.get("id")
            if type(row.get("started")) is int and row == {"event": "seal", "started": started}:
                sealed = True
            elif type(ticket) is int and row == {"event": "start", "id": started + 1}:
                started = ticket
            elif type(ticket) is int and 1 <= ticket <= started and ticket not in settled:
                tokens = row.get("input_tokens")
                if row != {"event": "settle", "id": ticket, "input_tokens": tokens}:
                    raise ValueError("settlement receipt")
                if tokens is not None and input_tokens(tokens) is None:
                    raise ValueError("settlement usage")
                settled[ticket] = input_tokens(tokens)
            else:
                raise ValueError("receipt sequence")
    except (ValueError, RecursionError):
        issue = "broker receipts inconsistent"
    # A missing seal or outstanding request means spending may have exceeded the known usage.
    if issue is None and (not sealed or not raw.endswith(b"\n") or len(settled) != started):
        issue = "broker receipts incomplete"
    if issue is None and any(tokens is None for tokens in settled.values()):
        issue = "broker receipts missing upstream usage"
    return ReceiptUsage(tuple(tokens for tokens in settled.values() if tokens is not None), issue)
