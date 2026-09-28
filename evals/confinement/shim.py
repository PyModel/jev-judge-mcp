"""The confined agent's local HTTP forwarder:
`python3 shim.py --listen H:P --upstream S://H:P --sock PATH --cap-file PATH`.

Runs inside the agent container, where the network namespace is `none` and loopback is the only
interface. The agent's runtime (the model client, the Jev MCP server pointed at
`TYPESAFE_BASE_URL=http://127.0.0.1:<port>`) speaks plain HTTP to this shim; the shim reframes each
request onto the broker's Unix socket and relays the broker's response. It holds no credential: the
capability token comes from the read-only grant file the harness mounts, the client's own
credential headers are stripped before framing (the container's config carries only a placeholder),
and the broker injects the real key.
"""

import argparse
import http.server
import json
import socket
import sys
from collections.abc import Sequence
from typing import Any

try:  # Package import on the host; sibling import when mounted flat into the agent container.
    from evals.confinement import protocol
except ImportError:  # pragma: no cover - the container path, exercised by the Docker tests
    import protocol

CONNECT_TIMEOUT_S = 10.0
FRAME_TIMEOUT_S = 300.0
"""Generous: one jev call can outwait the broker's upstream budget; the run timeout bounds the rest."""

ERROR_STATUS = {"capability": 403, "allowlist": 403, "upstream": 502, "protocol": 400, "internal": 500}


class _Forwarder(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"
    """One request per connection: no keep-alive bookkeeping, and every client tolerates the close."""

    upstream: tuple[str, str, int] = ("https", "", 0)
    sock_path = ""
    cap_path = ""

    def do_GET(self) -> None:
        self._forward()

    def do_POST(self) -> None:
        self._forward()

    def _capability(self) -> str:
        with open(self.cap_path, encoding="utf-8") as handle:
            return str(json.load(handle).get("token", ""))

    def _forward(self) -> None:
        try:
            body = self.rfile.read(int(self.headers.get("Content-Length") or 0)) if self.command == "POST" else b""
            if len(body) > protocol.MAX_BODY_BYTES:
                self._fail(400, "request body exceeds the cap")
                return
            headers = [
                [name, value]
                for name, value in self.headers.items()
                if name.lower() not in protocol.CREDENTIAL_REQUEST_HEADERS
                and name.lower() not in protocol.HOP_BY_HOP_HEADERS
                and name.lower() != "host"
            ]
            scheme, host, port = self.upstream
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as upstream:
                upstream.settimeout(CONNECT_TIMEOUT_S)
                upstream.connect(self.sock_path)
                upstream.settimeout(FRAME_TIMEOUT_S)
                protocol.send_frame(
                    upstream,
                    {
                        "cap": self._capability(),
                        "method": self.command,
                        "scheme": scheme,
                        "host": host,
                        "port": port,
                        "path": self.path,
                        "headers": headers,
                    },
                    body,
                )
                header, body_out = protocol.recv_frame(upstream)
        except (OSError, protocol.ProtocolError, ValueError) as error:
            self._fail(502, f"broker unreachable: {type(error).__name__}")
            return
        if "error" in header:
            self._fail(ERROR_STATUS.get(str(header["error"]), 502), str(header.get("detail", "refused")))
            return
        status = int(header.get("status", 502))
        self.send_response(status)
        for name, value in header.get("headers", []):  # pyright: ignore[reportUnknownVariableType]
            if str(name).lower() in protocol.HOP_BY_HOP_HEADERS or str(name).lower() == "content-length":
                continue
            self.send_header(str(name), str(value))
        self.send_header("Content-Length", str(len(body_out)))
        self.end_headers()
        self.wfile.write(body_out)

    def _fail(self, status: int, detail: str) -> None:
        payload = json.dumps({"error": "broker", "detail": detail}).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args: Any) -> None:
        """The shim keeps no log: a transcript of requests is the broker's receipt business."""


def parse_upstream(text: str) -> tuple[str, str, int]:
    scheme, _, rest = text.partition("://")
    if not rest:
        raise SystemExit(f"--upstream must be scheme://host:port, got {text!r}")
    host, _, port_text = rest.partition(":")
    return (scheme or "https", host, int(port_text or 443))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="shim.py", description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--listen", required=True, help="host:port to serve HTTP on (loopback)")
    parser.add_argument("--upstream", required=True, help="scheme://host:port of the single upstream")
    parser.add_argument("--sock", required=True, help="path to the broker's Unix socket")
    parser.add_argument("--cap-file", required=True, help="path to this run's capability grant file")
    args = parser.parse_args(argv)
    host, _, port_text = args.listen.partition(":")
    _Forwarder.upstream = parse_upstream(args.upstream)
    _Forwarder.sock_path = args.sock
    _Forwarder.cap_path = args.cap_file
    server = http.server.ThreadingHTTPServer((host or "127.0.0.1", int(port_text)), _Forwarder)
    sys.stderr.write(json.dumps({"shim": args.listen, "upstream": args.upstream}) + "\n")
    sys.stderr.flush()
    server.serve_forever(poll_interval=0.2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
