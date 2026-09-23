"""A loopback Jev-compatible endpoint for tests that spawn the server: no network beyond 127.0.0.1.

The server under test is pointed at it with `JEV_API_BASE_URL`, `JEV_API_KEY` and
`JEV_PROVIDER=compatible`. What it does with a request is chosen by the request's state: a jev_screen
`content` of `echo:<n>` answers with injection probability `n/1000` (a per-call marker), `hang:<tag>`
never answers and records whether the client aborted, and `reflect` returns 401 whose body starts
with one of the `reflect` strings (e.g. a configured secret; the n-th request reflects the n-th, so
each lands before the 200-unit cut), then the request's Authorization header, path, and body.
`number:<literal>` answers every probability with that raw JSON literal (`1e400`, `NaN`). `reset`
closes the socket without a reply, and `garbage` sends a half-written response.
`Peer(reflect_all=True)` reflects every request whatever its shape, for providers whose envelope is
not `{model, state, questions}`.
"""

import json
import select
import socket
import struct
import threading
import time
from collections.abc import Iterable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Self, cast, override

API_KEY = "peer-api-key-0001"


class Peer:
    def __init__(self, reflect: Iterable[str] = (), delay: float = 0.0, reflect_all: bool = False) -> None:
        self.reflect = tuple(reflect)
        """Extra strings a `reflect` reply echoes, e.g. every secret in the server's environment."""
        self.reflect_all = reflect_all
        self.delay = delay
        self.aborted: set[str] = set()
        self.hanging: set[str] = set()
        self.requests = 0
        self.contents: list[str] = []
        """Every jev_screen `content` received, in arrival order."""
        self._lock = threading.Lock()
        peer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_POST(self) -> None:
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                with peer._lock:
                    index = peer.requests
                    peer.requests += 1
                peer.handle(self, body, index)

            @override
            def log_message(self, format: str, *args: Any) -> None:
                pass

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self._server.server_address[1]}/v1/systemone"

    def env(self) -> dict[str, str]:
        return {"JEV_PROVIDER": "compatible", "JEV_API_KEY": API_KEY, "JEV_API_BASE_URL": self.url}

    def handle(self, handler: BaseHTTPRequestHandler, body: Any, index: int) -> None:
        state: Any = cast(dict[str, Any], body).get("state") if isinstance(body, dict) else None
        content = str(cast(dict[str, Any], state).get("content", "")) if isinstance(state, dict) else ""
        with self._lock:
            self.contents.append(content)
        kind, _, tag = ("reflect", "", "") if self.reflect_all else content.partition(":")
        if kind == "echo":
            time.sleep(self.delay)
            answers = {
                "injection": {"noul": int(tag) / 1000},
                "substance": {"noul": 0.99},
                "relevance": {"noul": 0.99},
            }
            self._reply(handler, 200, json.dumps({"answers": answers, "model": body["model"]}).encode())
        elif kind == "hang":
            with self._lock:
                self.hanging.add(tag)
            if _wait_for_close(handler.connection, timeout=30):
                with self._lock:
                    self.aborted.add(tag)
        elif kind == "number":
            # A raw JSON literal for every probability: `1e400` parses as infinity, `NaN` is not JSON.
            probabilities = ", ".join(f'"{key}": {{"noul": {tag}}}' for key in ("injection", "substance", "relevance"))
            self._reply(handler, 200, f'{{"answers": {{{probabilities}}}}}'.encode())
        elif kind == "reflect":
            first = self.reflect[index % len(self.reflect)] if self.reflect else ""
            echoed = f"{first} {handler.headers['Authorization']} {handler.path} {json.dumps(body)}"
            self._reply(handler, 401, echoed.encode())
        elif kind == "reset":
            handler.connection.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
            handler.close_connection = True
        elif kind == "garbage":
            handler.wfile.write(b'HTTP/1.1 200 OK\r\nContent-Length: 1000\r\n\r\n{"answers": {')
            handler.wfile.flush()
            handler.close_connection = True
        else:
            self._reply(handler, 400, b"unknown peer command")

    @staticmethod
    def _reply(handler: BaseHTTPRequestHandler, status: int, content: bytes) -> None:
        handler.send_response(status)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", str(len(content)))
        handler.end_headers()
        handler.wfile.write(content)

    def __enter__(self) -> Self:
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._server.shutdown()
        self._server.server_close()


def _wait_for_close(connection: socket.socket, timeout: float) -> bool:
    """Block until the client closes its end; `True` if it did within `timeout`."""
    deadline = time.monotonic() + timeout
    while (remaining := deadline - time.monotonic()) > 0:
        readable, _, _ = select.select([connection], [], [], remaining)
        if readable:
            try:
                return connection.recv(1, socket.MSG_PEEK) == b""
            except OSError:
                return True
    return False
