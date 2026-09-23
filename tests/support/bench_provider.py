"""A loopback Jev-compatible endpoint for the bench dry run: 127.0.0.1 only, no key that works anywhere.

The Jev server under test is pointed at it with `env()` (`JEV_PROVIDER=compatible`). Every noul
answers 0.02 for an `injection*` question and 0.98 otherwise; every choice picks its first criterion
with confidence 0.95. `model` is echoed in the body, which replaces the requested model in the tool
result, so `Loopback(model="jev-latest")` stands for a wrong pin; `Loopback(status=500)` fails every call.
"""

import json
import threading
from contextlib import AbstractContextManager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Self, cast, override

API_KEY = "bench-loopback-key-0001"


def answer(kind: str, question_id: str, criteria: dict[str, Any]) -> dict[str, Any]:
    if kind == "noul":
        return {"noul": 0.02 if question_id.startswith("injection") else 0.98}
    if kind != "choice":
        raise ValueError(f"the loopback answers noul and choice questions, not {kind}")
    keys = list(criteria)
    rest = 0.04 / max(1, len(keys) - 1)
    return {
        "choice": keys[0],
        "confidence": 0.95,
        "probabilities": {k: 0.96 if i == 0 else rest for i, k in enumerate(keys)},
    }


class Loopback:
    def __init__(self, model: str = "jev-1.13.0", status: int = 200) -> None:
        self.model = model
        self.status = status
        self.requests = 0
        self.lock: AbstractContextManager[object] = threading.Lock()
        """Guards `requests`: the server handles each connection on its own thread."""
        loopback = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_POST(self) -> None:
                body = cast(dict[str, Any], json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
                with loopback.lock:
                    loopback.requests += 1
                if loopback.status != 200:
                    self._reply(loopback.status, b'{"error": "loopback failure"}')
                    return
                questions = cast(dict[str, dict[str, Any]], body["questions"])
                answers = {qid: answer(q["type"], qid, q.get("criteria") or {}) for qid, q in questions.items()}
                usage = {"input_tokens": 120, "output_tokens": 0}
                self._reply(200, json.dumps({"answers": answers, "model": loopback.model, "usage": usage}).encode())

            def _reply(self, status: int, content: bytes) -> None:
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)

            @override
            def log_message(self, format: str, *args: Any) -> None:
                pass

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def env(self) -> dict[str, str]:
        url = f"http://127.0.0.1:{self._server.server_address[1]}/v1/systemone"
        return {"JEV_PROVIDER": "compatible", "JEV_API_KEY": API_KEY, "JEV_API_BASE_URL": url}

    def __enter__(self) -> Self:
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._server.shutdown()
        self._server.server_close()
