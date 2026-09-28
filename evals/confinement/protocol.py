"""The framed JSON protocol the confined agent's shim and the broker speak over a Unix socket.

One request and one response per connection: a single JSON header line (UTF-8, capped), then
exactly `body_len` raw bytes. Both sides are stdlib-only and run inside containers (the broker
sidecar and the agent's own namespace), so this module imports nothing from `evals`.

The header never carries a credential: the request's `cap` is the run capability (not the key),
the broker injects the real credential itself, and `CREDENTIAL_REQUEST_HEADERS` names what the
shim strips from the client's HTTP headers so a placeholder can never leak upstream.
"""

import json
import socket
from typing import Any, cast

PROTOCOL_VERSION = 1
"""Bumped on any incompatible change to the header shape below."""

MAX_HEADER_BYTES = 64 * 1024
MAX_BODY_BYTES = 16 * 1024 * 1024
"""Per-direction body cap. A jev request is bounded well under this (limits.py); anything larger
is a misbehaving client, and the refusal is the cap, not a crash."""

CREDENTIAL_REQUEST_HEADERS = frozenset({"authorization", "proxy-authorization", "x-api-key", "api-key", "cookie"})
"""Headers the shim strips from every client request. The real credential is injected broker-side."""

HOP_BY_HOP_HEADERS = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
    }
)
"""Never forwarded in either direction; `Content-Length` is recomputed by each writer."""

REDACTED = b"[REDACTED]"
"""What replaces a credential value an upstream echoed back."""


class ProtocolError(Exception):
    """A malformed or oversized frame. The connection is closed; nothing is forwarded."""


def _recv_exact(sock: socket.socket, count: int) -> bytes:
    chunks: list[bytes] = []
    remaining = count
    while remaining > 0:
        chunk = sock.recv(min(remaining, 65_536))
        if not chunk:
            raise ProtocolError("connection closed inside a frame")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _recv_header(sock: socket.socket) -> dict[str, Any]:
    line = bytearray()
    while not line.endswith(b"\n"):
        piece = sock.recv(1)
        if not piece:
            raise ProtocolError("connection closed inside the header")
        line += piece
        if len(line) > MAX_HEADER_BYTES:
            raise ProtocolError("header exceeds the protocol cap")
    text = bytes(line).decode("utf-8", errors="strict").strip()
    loaded: object = json.loads(text)
    if not isinstance(loaded, dict):
        raise ProtocolError("not a v1 frame header")
    header = cast(dict[str, Any], loaded)
    if header.get("v") != PROTOCOL_VERSION:
        raise ProtocolError("not a v1 frame header")
    return header


def send_frame(sock: socket.socket, header: dict[str, Any], body: bytes) -> None:
    """One header line plus `body`. The header's `body_len` is set here; callers never size it."""
    if len(body) > MAX_BODY_BYTES:
        raise ProtocolError("body exceeds the protocol cap")
    payload = dict(header)
    payload["v"] = PROTOCOL_VERSION
    payload["body_len"] = len(body)
    line = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    if len(line) > MAX_HEADER_BYTES:
        raise ProtocolError("header exceeds the protocol cap")
    sock.sendall(line + b"\n" + body)


def recv_frame(sock: socket.socket) -> tuple[dict[str, Any], bytes]:
    """Read one frame: `(header, body)`. Raises `ProtocolError` on malformed or oversized input."""
    header = _recv_header(sock)
    length = header.get("body_len")
    if not isinstance(length, int) or length < 0 or length > MAX_BODY_BYTES:
        raise ProtocolError("body_len is missing or out of range")
    return header, _recv_exact(sock, length)
