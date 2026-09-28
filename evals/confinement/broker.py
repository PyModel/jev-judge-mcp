"""The broker sidecar: the only process that holds the TypeSafe key and the agent model-provider key.

`python3 broker.py CONFIG` reads a JSON config (below), listens on a Unix socket inside a shared
docker volume, and forwards each allowlisted request to its upstream with the real credential
injected. It never returns, logs, or echoes a credential: client credential headers are stripped
(the agent's config carries only a placeholder), upstream responses are scanned and redacted, and
every receipt and refusal is one JSON line on stderr naming method/host/path/status only.

Config::

    {
      "listen": "/sockets/broker.sock",
      "grants_dir": "/grants",
      "upstreams": [
        {"name": "typesafe", "scheme": "https", "host": "api.typesafe.ai", "port": 443,
         "methods": ["POST"], "paths": ["/v1/systemone"],
         "credential_file": "/secrets/typesafe.key",
         "credential_header": "Authorization", "credential_scheme": "Bearer "},
        ...
      ]
    }

`paths` entries match exactly, or end in `*` for a prefix match; the model-provider upstream pins
its host and lets its paths through (`"*"`), which is the acceptance level the study names. A
capability grant is one JSON file per run in `grants_dir` — `{"token": ..., "expires": <epoch>}` —
written by the harness, removed at run end; the broker re-reads the grant per request, so removal
is revocation, and `expires` bounds a grant the harness could not remove. Grant file names are
`sha256(token).hex + ".json"`, so checking a presented token needs no grant listing.

Egress beyond the allowlist cannot leave this process: destination, method, and path are each
checked before any socket is opened, and the refusal is logged (without the token or key) and
returned to the client. The container's network is the docker bridge; the allowlist here is the
egress rule the acceptance criteria name.
"""

import argparse
import hashlib
import hmac
import http.client
import json
import os
import signal
import socket
import socketserver
import sys
import threading
import time
from collections.abc import Sequence
from typing import Any, cast

try:  # Package import on the host; sibling import when mounted flat into the sidecar.
    from evals.confinement import protocol
except ImportError:  # pragma: no cover - the container path, exercised by the Docker tests
    import protocol

UPSTREAM_TIMEOUT_S = 180.0
"""One forwarded request's connect+read budget. The run's wall timeout bounds the whole study."""

SOCKET_MODE = 0o666
"""The shared volume is the only reader path; the capability check, not file mode, is the gate."""

HOP = protocol.HOP_BY_HOP_HEADERS | {"content-length", "host"}
"""Never forwarded to the upstream; the broker is the hop that recomputes framing."""


class Refused(Exception):
    """An allowlist or capability refusal. `kind` maps to an HTTP status at the shim."""

    def __init__(self, kind: str, detail: str) -> None:
        super().__init__(detail)
        self.kind = kind
        self.detail = detail


def _sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _cap_id(token: str) -> str:
    """A stable, non-reversible handle for receipts. The token itself is never logged."""
    return _sha256_hex(token)[:8]


class Broker:
    def __init__(self, config: dict[str, Any]) -> None:
        self.listen_path = str(config["listen"])
        self.grants_dir = str(config["grants_dir"])
        self.credentials: list[tuple[str, bytes]] = []
        """Every credential value loaded, for the response redaction scan."""
        self.upstreams: dict[tuple[str, str, int], dict[str, Any]] = {}
        for upstream in config["upstreams"]:
            entry = dict(upstream)
            host = str(entry["host"]).lower()
            key = (str(entry.get("scheme", "https")).lower(), host, int(entry.get("port", 443)))
            if key in self.upstreams:
                raise SystemExit(f"duplicate upstream {key}")
            path = str(entry["credential_file"])
            with open(path, encoding="utf-8-sig") as handle:
                value = handle.read().strip()
            if not value:
                raise SystemExit(f"credential file {path} is empty")
            entry["credential"] = value
            self.credentials.append((str(entry["name"]), value.encode("utf-8")))
            self.upstreams[key] = entry

    # --- capability grants ---------------------------------------------------------

    def _grant_for(self, token: str) -> dict[str, Any]:
        path = os.path.join(self.grants_dir, _sha256_hex(token) + ".json")
        try:
            with open(path, encoding="utf-8") as handle:
                loaded: object = json.load(handle)
        except (OSError, json.JSONDecodeError):
            raise Refused("capability", "capability not recognized") from None
        if not isinstance(loaded, dict):
            raise Refused("capability", "capability not recognized")
        grant = cast(dict[str, Any], loaded)
        if not hmac.compare_digest(str(grant.get("token")), token):
            raise Refused("capability", "capability not recognized")
        return grant

    def check_capability(self, token: str, now: float) -> dict[str, Any]:
        grant = self._grant_for(token)
        expires = grant.get("expires")
        if not isinstance(expires, (int, float)) or now > float(expires):
            raise Refused("capability", "capability expired")
        return grant

    # --- allowlist -------------------------------------------------------------------

    def _match_upstream(self, scheme: str, host: str, port: int) -> dict[str, Any]:
        key = (scheme.lower(), host.lower(), port)
        upstream = self.upstreams.get(key)
        if upstream is None:
            raise Refused("allowlist", f"destination {scheme}://{host}:{port} is not allowlisted")
        return upstream

    @staticmethod
    def _origin_form(path: str) -> bool:
        """Only origin-form paths cross: a leading `/`, no scheme, authority, dot-segment, or
        percent-encoded slash/dot (F3). An absolute URI here would be forwarded under the
        allowlisted host's credential while naming another authority — refused instead."""
        if not path.startswith("/") or path.startswith("//"):
            return False
        lowered = path.lower()
        return not any(marker in lowered for marker in ("://", "..", "@", "%2f", "%2e", "\\", " "))

    def check_allowlist(self, method: str, scheme: str, host: str, port: int, path: str) -> dict[str, Any]:
        upstream = self._match_upstream(scheme, host, port)
        if method.upper() not in {str(m).upper() for m in upstream["methods"]}:
            raise Refused("allowlist", f"method {method.upper()} is not allowlisted")
        if not self._origin_form(path):
            raise Refused("allowlist", f"path {path[:120]} is not origin-form")
        if path not in {str(rule) for rule in upstream["paths"]}:
            raise Refused("allowlist", f"path {path} is not allowlisted")
        return upstream

    # --- forwarding --------------------------------------------------------------------

    @staticmethod
    def _strip(headers: Sequence[Sequence[str]]) -> list[list[str]]:
        kept: list[list[str]] = []
        for name, value in headers:
            lowered = name.lower()
            if lowered in protocol.CREDENTIAL_REQUEST_HEADERS or lowered in HOP:
                continue
            if lowered in protocol.METHOD_OVERRIDE_HEADERS:
                continue  # F6: the allowlisted method is the method; overrides never cross
            if lowered == "accept-encoding":
                continue  # F6: the broker reads bytes it must scan, so it asks for identity
            kept.append([name, value])
        kept.append(["Accept-Encoding", "identity"])
        return kept

    def _redact(self, body: bytes, headers: list[list[str]]) -> tuple[bytes, list[list[str]]]:
        out = body
        for _, secret in self.credentials:
            if secret and secret in out:
                out = out.replace(secret, protocol.REDACTED)
            for pair in headers:
                if secret and secret in pair[1].encode("utf-8"):
                    pair[1] = pair[1].encode("utf-8").replace(secret, protocol.REDACTED).decode("utf-8", "replace")
        return out, headers

    def forward(
        self, method: str, scheme: str, host: str, port: int, path: str, headers: Sequence[Sequence[str]], body: bytes
    ) -> tuple[int, list[list[str]], bytes]:
        upstream = self.check_allowlist(method, scheme, host, port, path)
        sent = self._strip(headers)
        credential = str(upstream["credential"])
        header_name = str(upstream.get("credential_header", "Authorization"))
        scheme_prefix = str(upstream.get("credential_scheme", "Bearer "))
        sent.append([header_name, f"{scheme_prefix}{credential}" if scheme_prefix else credential])
        connection = (
            http.client.HTTPSConnection(host, port, timeout=UPSTREAM_TIMEOUT_S)
            if scheme.lower() == "https"
            else http.client.HTTPConnection(host, port, timeout=UPSTREAM_TIMEOUT_S)
        )
        try:
            connection.request(
                method.upper(),
                str(upstream.get("base_path", "")) + path,
                body=body,
                headers={name: value for name, value in sent},
            )
            response = connection.getresponse()
            encoding = response.getheader("Content-Encoding")
            if encoding and encoding.strip().lower() not in ("identity", ""):
                # F6: a compressed body cannot be scanned for a credential echo, so it never
                # crosses back; the request asked for identity, so an encoding is a refusal.
                raise Refused("upstream", f"upstream returned {encoding.strip()} content encoding")
            raw = response.read(protocol.MAX_BODY_BYTES + 1)
            if len(raw) > protocol.MAX_BODY_BYTES:
                raise Refused("upstream", "upstream response exceeds the protocol cap")
            received = [[name, value] for name, value in response.getheaders() if name.lower() not in HOP]
            body_out, headers_out = self._redact(raw, received)
            return response.status, headers_out, body_out
        except OSError as error:
            raise Refused("upstream", f"upstream unreachable: {type(error).__name__}") from None
        finally:
            connection.close()

    # --- serving ------------------------------------------------------------------------

    def handle(self, request: socket.socket) -> None:
        token = ""
        receipt: dict[str, Any] = {}
        try:
            request.settimeout(UPSTREAM_TIMEOUT_S)
            header, body = protocol.recv_frame(request)
            token = str(header.get("cap", ""))
            receipt = {
                "ts": round(time.time(), 3),
                "cap": _cap_id(token) if token else "-",
                "method": str(header.get("method", "")),
                "host": str(header.get("host", "")),
                "path": str(header.get("path", ""))[:200],
            }
            self.check_capability(token, time.time())
            status, headers, body_out = self.forward(
                str(header.get("method", "")),
                str(header.get("scheme", "https")),
                str(header.get("host", "")),
                int(header.get("port", 443)),
                str(header.get("path", "/")),
                [(str(n), str(v)) for n, v in header.get("headers", [])],
                body,
            )
            receipt.update({"status": status, "resp_bytes": len(body_out)})
            protocol.send_frame(request, {"status": status, "headers": headers}, body_out)
        except Refused as refused:
            receipt.update({"refused": refused.kind, "detail": refused.detail})
            _emit(receipt)
            try:
                protocol.send_frame(request, {"error": refused.kind, "detail": refused.detail}, b"")
            except OSError:
                pass
            return
        except protocol.ProtocolError as error:
            _emit(
                {
                    "ts": round(time.time(), 3),
                    "cap": _cap_id(token) if token else "-",
                    "refused": "protocol",
                    "detail": str(error),
                }
            )
            return
        except Exception as error:
            receipt.update({"refused": "internal", "detail": f"{type(error).__name__}"})
            _emit(receipt)
            return
        _emit(receipt)


def _emit(receipt: dict[str, Any]) -> None:
    """One JSON line on stderr. No header values, no bodies, no tokens, no keys — ever."""
    sys.stderr.write(json.dumps(receipt, separators=(",", ":")) + "\n")
    sys.stderr.flush()


class _Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    """Serves each connection through `Broker.handle`; no per-connection handler class exists."""

    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, broker: Broker) -> None:
        self.broker = broker
        if os.path.exists(broker.listen_path):
            os.unlink(broker.listen_path)
        # The handler class is unused: `finish_request` dispatches straight to the broker.
        super().__init__(broker.listen_path, socketserver.BaseRequestHandler)

    def finish_request(self, request: Any, client_address: Any) -> None:  # pyright: ignore[reportIncompatibleMethodOverride] - the socketserver base types its socket loosely; this server hands the raw socket to the broker
        self.broker.handle(request)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="broker.py", description=(__doc__ or "").splitlines()[0])
    parser.add_argument("config", help="path to the JSON config")
    args = parser.parse_args(argv)
    with open(args.config, encoding="utf-8") as handle:
        config = json.load(handle)
    broker = Broker(config)
    deadline = float(config.get("max_lifetime_s") or 0)
    if deadline <= 0:
        raise SystemExit("config needs max_lifetime_s: the broker bounds its own life (ADR-0074)")
    os.makedirs(os.path.dirname(broker.listen_path) or ".", exist_ok=True)
    server = _Server(broker)
    os.chmod(broker.listen_path, SOCKET_MODE)

    def stop(signum: int, _frame: Any) -> None:
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    # F5: the broker never outlives its study. The deadline fires even if the harness dies: a
    # killed run leaves the sidecar holding the key mounts for at most this long.
    timer = threading.Timer(deadline, lambda: stop(0, None))
    timer.daemon = True
    timer.start()
    _emit({"ts": round(time.time(), 3), "listening": broker.listen_path})
    try:
        server.serve_forever(poll_interval=0.2)
    finally:
        timer.cancel()
        server.server_close()
        try:
            os.unlink(broker.listen_path)
        except OSError:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
