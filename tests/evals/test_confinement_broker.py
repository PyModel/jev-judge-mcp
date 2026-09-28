"""The broker sidecar offline: allowlist, capabilities, credential handling, and receipts.

The broker process runs for real (a subprocess over a Unix socket in a tmpdir) against a local
fake upstream, so every rule here is the wire behavior a confined run depends on, not a mock of
the module's internals. Docker is not involved: the same stdlib file is what the sidecar container
mounts, and the Docker-marked adversarial suite covers the container path end to end.
"""

import json
import shutil
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, ClassVar

import pytest

from evals.confinement import protocol
from evals.confinement.launch import CLOCK, mint_capability

REPO = Path(__file__).resolve().parents[2]
BROKER = REPO / "evals" / "confinement" / "broker.py"

KEY = "sk-broker-fake-typesafe-key-0123456789abcdefNOTREAL"
PROVIDER_KEY = "sk-broker-fake-provider-key-0123456789abcdefNOTREAL"


class _Upstream(BaseHTTPRequestHandler):
    """Records every request; answers a configurable JSON body. Echoes the key on demand."""

    seen: ClassVar[list[dict[str, Any]]] = []
    echo_key = False

    def _record(self, body: bytes) -> None:
        self.seen.append(
            {
                "method": self.command,
                "path": self.path,
                "authorization": self.headers.get("Authorization"),
                "x_api_key": self.headers.get("x-api-key"),
                "content_type": self.headers.get("Content-Type"),
                "accept_encoding": self.headers.get("Accept-Encoding"),
                "method_override": self.headers.get("X-HTTP-Method-Override"),
                "body": body.decode("utf-8", "replace"),
            }
        )

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        self._record(body)
        answer = {"answers": {"q": {"choice": "a", "confidence": 0.99, "probabilities": {"a": 0.99, "b": 0.01}}}}
        payload = json.dumps(answer).encode()
        if _Upstream.echo_key:
            payload = json.dumps({"note": f"your key is {KEY}", "answers": answer["answers"]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("X-Echo", f"bearer {KEY}")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:
        self._record(b"")
        self.send_response(200)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"{}")

    def log_message(self, format: str, *args: Any) -> None:
        pass


def _start_upstream() -> tuple[ThreadingHTTPServer, int]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Upstream)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, server.server_address[1]


def _call(
    sock_path: str,
    cap: str,
    method: str,
    host: str,
    path: str,
    *,
    scheme: str = "http",
    port: int = 0,
    headers: list[list[str]] | None = None,
    body: bytes = b"",
) -> tuple[dict[str, Any], bytes]:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(30)
        sock.connect(sock_path)
        protocol.send_frame(
            sock,
            {
                "cap": cap,
                "method": method,
                "scheme": scheme,
                "host": host,
                "port": port,
                "path": path,
                "headers": headers or [],
            },
            body,
        )
        return protocol.recv_frame(sock)


@pytest.fixture()
def stack(tmp_path: Path) -> Any:
    """A running broker against two fake upstreams (one per allowlisted destination).

    The socket lives under a short `/tmp` root: macOS caps an AF_UNIX path at 104 bytes, which a
    pytest tmp_path (deep under /private/var/folders) can exceed."""
    import tempfile

    short_root = Path(tempfile.mkdtemp(prefix="jevbrk-", dir="/tmp"))
    server, port = _start_upstream()
    provider_server, provider_port = _start_upstream()
    grants = short_root / "grants"
    grants.mkdir()
    key_file = short_root / "typesafe.key"
    key_file.write_text(KEY + "\n", encoding="utf-8")
    provider_key_file = short_root / "provider.key"
    provider_key_file.write_text(PROVIDER_KEY + "\n", encoding="utf-8")
    sock_path = short_root / "broker.sock"
    config = {
        "listen": str(sock_path),
        "grants_dir": str(grants),
        "upstreams": [
            {
                "name": "typesafe",
                "scheme": "http",
                "host": "127.0.0.1",
                "port": port,
                "methods": ["POST"],
                "paths": ["/v1/systemone"],
                "credential_file": str(key_file),
                "credential_header": "Authorization",
                "credential_scheme": "Bearer ",
            },
            {
                "name": "model-provider",
                "scheme": "http",
                "host": "127.0.0.1",
                "port": provider_port,
                "methods": ["POST", "GET"],
                "paths": ["/chat/completions", "/models"],
                "base_path": "/zen/go/v1",
                "credential_file": str(provider_key_file),
                "credential_header": "Authorization",
                "credential_scheme": "Bearer ",
            },
        ],
        "max_lifetime_s": 3600,
    }
    config_path = short_root / "broker-config.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    log_path = short_root / "broker.log"
    log = open(log_path, "w")
    proc = subprocess.Popen([sys.executable, str(BROKER), str(config_path)], stdout=subprocess.DEVNULL, stderr=log)
    deadline = time.time() + 20
    while not sock_path.exists():
        if proc.poll() is not None:
            log.close()
            raise AssertionError(f"broker exited at startup: {log_path.read_text()}")
        if time.time() > deadline:
            proc.kill()
            log.close()
            raise AssertionError("broker socket never appeared")
        time.sleep(0.05)
    try:
        yield {
            "proc": proc,
            "sock": str(sock_path),
            "port": port,
            "provider_port": provider_port,
            "grants": grants,
            "log": log_path,
        }
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        log.close()
        server.shutdown()
        provider_server.shutdown()
        shutil.rmtree(short_root, ignore_errors=True)


def test_an_allowlisted_call_relays_and_injects_the_real_key(stack: Any) -> None:
    cap = mint_capability(stack["grants"], 60)
    header, body = _call(
        stack["sock"],
        cap.token,
        "POST",
        "127.0.0.1",
        "/v1/systemone",
        port=stack["port"],
        headers=[["Content-Type", "application/json"]],
        body=b'{"state": {}, "questions": {}}',
    )
    assert header.get("status") == 200
    assert b'"answers"' in body
    seen = _Upstream.seen[-1]
    assert seen["authorization"] == f"Bearer {KEY}"
    assert seen["path"] == "/v1/systemone"


def test_the_clients_placeholder_never_reaches_the_upstream(stack: Any) -> None:
    cap = mint_capability(stack["grants"], 60)
    _call(
        stack["sock"],
        cap.token,
        "POST",
        "127.0.0.1",
        "/v1/systemone",
        port=stack["port"],
        headers=[["Authorization", "Bearer confined-placeholder-would-be-here"]],
    )
    seen = _Upstream.seen[-1]
    assert seen["authorization"] == f"Bearer {KEY}", "the injected credential replaces the client's"


def test_an_upstream_echo_of_the_key_is_redacted_before_the_client_sees_it(stack: Any) -> None:
    _Upstream.echo_key = True
    try:
        cap = mint_capability(stack["grants"], 60)
        header, body = _call(stack["sock"], cap.token, "POST", "127.0.0.1", "/v1/systemone", port=stack["port"])
        assert KEY.encode() not in body
        assert protocol.REDACTED in body
        echoed = [value for name, value in header.get("headers", []) if name == "X-Echo"]
        assert echoed == [f"bearer {protocol.REDACTED.decode()}"]
    finally:
        _Upstream.echo_key = False


@pytest.mark.parametrize(
    ("method", "host", "path", "detail"),
    [
        ("POST", "evil.example", "/v1/systemone", "destination"),
        ("GET", "127.0.0.1", "/v1/systemone", "method"),
        ("POST", "127.0.0.1", "/v1/other", "path"),
    ],
)
def test_non_allowlisted_operations_are_refused_without_an_upstream_call(
    stack: Any, method: str, host: str, path: str, detail: str
) -> None:
    cap = mint_capability(stack["grants"], 60)
    before = len(_Upstream.seen)
    header, _ = _call(stack["sock"], cap.token, method, host, path, port=stack["port"])
    assert header.get("error") == "allowlist"
    assert detail in str(header.get("detail"))
    assert len(_Upstream.seen) == before


def test_the_provider_keeps_its_base_path_and_exact_endpoints(stack: Any) -> None:
    """F3: the base URL's path prefix is prepended, only the API family's endpoints cross, and
    anything else on the same host is refused."""
    cap = mint_capability(stack["grants"], 60)
    header, _ = _call(stack["sock"], cap.token, "POST", "127.0.0.1", "/chat/completions", port=stack["provider_port"])
    assert header.get("status") == 200
    assert _Upstream.seen[-1]["path"] == "/zen/go/v1/chat/completions"
    assert _Upstream.seen[-1]["authorization"] == f"Bearer {PROVIDER_KEY}"
    header, _ = _call(stack["sock"], cap.token, "GET", "127.0.0.1", "/models", port=stack["provider_port"])
    assert header.get("status") == 200
    assert _Upstream.seen[-1]["path"] == "/zen/go/v1/models"
    header, _ = _call(stack["sock"], cap.token, "POST", "127.0.0.1", "/embeddings", port=stack["provider_port"])
    assert header.get("error") == "allowlist"


def test_an_unknown_capability_is_refused(stack: Any) -> None:
    header, _ = _call(
        stack["sock"], "not-a-real-capability-token", "POST", "127.0.0.1", "/v1/systemone", port=stack["port"]
    )
    assert header.get("error") == "capability"
    assert "not recognized" in str(header.get("detail"))


def test_an_expired_capability_is_refused(stack: Any) -> None:
    import hashlib

    probe = "expired-but-real-capability-token-0123456789"
    grant = stack["grants"] / f"{hashlib.sha256(probe.encode()).hexdigest()}.json"
    grant.write_text(json.dumps({"token": probe, "expires": CLOCK() - 1}) + "\n", encoding="utf-8")
    header, _ = _call(stack["sock"], probe, "POST", "127.0.0.1", "/v1/systemone", port=stack["port"])
    assert header.get("error") == "capability"
    assert "expired" in str(header.get("detail"))


def test_a_revoked_capability_is_refused_on_the_next_call(stack: Any) -> None:
    cap = mint_capability(stack["grants"], 60)
    ok, _ = _call(stack["sock"], cap.token, "POST", "127.0.0.1", "/v1/systemone", port=stack["port"])
    assert ok.get("status") == 200
    cap.grant_path.unlink()
    header, _ = _call(stack["sock"], cap.token, "POST", "127.0.0.1", "/v1/systemone", port=stack["port"])
    assert header.get("error") == "capability"
    assert "not recognized" in str(header.get("detail"))


def test_an_oversized_body_is_refused_without_an_upstream_call(stack: Any) -> None:
    cap = mint_capability(stack["grants"], 60)
    before = len(_Upstream.seen)
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(30)
        sock.connect(stack["sock"])
        with pytest.raises(protocol.ProtocolError):
            protocol.send_frame(
                sock,
                {
                    "cap": cap.token,
                    "method": "POST",
                    "scheme": "http",
                    "host": "127.0.0.1",
                    "port": stack["port"],
                    "path": "/v1/systemone",
                    "headers": [],
                },
                b"x" * (protocol.MAX_BODY_BYTES + 1),
            )
    assert len(_Upstream.seen) == before


@pytest.mark.parametrize(
    "path",
    [
        "http://evil.example/steal",
        "//evil.example/x",
        "/v1/%2e%2e/systemone",
        "/v1/../v1/systemone",
        "https://api.typesafe.ai/v1/systemone",
    ],
)
def test_non_origin_form_paths_never_cross_even_on_an_allowlisted_host(stack: Any, path: str) -> None:
    """F3/F9: the destination fields may name the allowlisted host, but a path that is not
    origin-form — an absolute URI, a network-path, an encoded or plain dot-segment — is refused
    before any socket opens, so the credential cannot be re-aimed at another authority."""
    cap = mint_capability(stack["grants"], 60)
    before = len(_Upstream.seen)
    header, _ = _call(stack["sock"], cap.token, "POST", "127.0.0.1", path, port=stack["port"])
    assert header.get("error") == "allowlist"
    assert "origin-form" in str(header.get("detail"))
    assert len(_Upstream.seen) == before


def test_the_broker_forces_identity_encoding_and_strips_method_overrides(stack: Any) -> None:
    """F6: the upstream sees `Accept-Encoding: identity` and never a method override; the client
    cannot smuggle a method past the allowlist or a compressed echo past the redaction scan."""
    cap = mint_capability(stack["grants"], 60)
    _call(
        stack["sock"],
        cap.token,
        "POST",
        "127.0.0.1",
        "/v1/systemone",
        port=stack["port"],
        headers=[["Accept-Encoding", "gzip"], ["X-HTTP-Method-Override", "DELETE"]],
    )
    seen = _Upstream.seen[-1]
    assert seen["accept_encoding"] == "identity"
    assert seen["method_override"] is None


def test_an_encoded_upstream_response_is_refused_not_decoded(tmp_path: Path) -> None:
    """F6: a gzip body the broker cannot scan never crosses back, even when it carries the key."""
    import gzip
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class _Gzipper(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            payload = gzip.compress(json.dumps({"echo": KEY}).encode())
            self.send_response(200)
            self.send_header("Content-Encoding", "gzip")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format: str, *args: Any) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), _Gzipper)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    import tempfile

    short_root = Path(tempfile.mkdtemp(prefix="jevbrk-", dir="/tmp"))
    try:
        key_file = short_root / "typesafe.key"
        key_file.write_text(KEY + "\n", encoding="utf-8")
        grants = short_root / "grants"
        grants.mkdir()
        sock_path = short_root / "broker.sock"
        config = {
            "listen": str(sock_path),
            "grants_dir": str(grants),
            "max_lifetime_s": 600,
            "upstreams": [
                {
                    "name": "typesafe",
                    "scheme": "http",
                    "host": "127.0.0.1",
                    "port": server.server_address[1],
                    "methods": ["POST"],
                    "paths": ["/v1/systemone"],
                    "credential_file": str(key_file),
                }
            ],
        }
        config_path = short_root / "broker-config.json"
        config_path.write_text(json.dumps(config), encoding="utf-8")
        proc = subprocess.Popen(
            [sys.executable, str(BROKER), str(config_path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        deadline = time.time() + 20
        while not sock_path.exists() and time.time() < deadline:
            time.sleep(0.05)
        cap = mint_capability(grants, 60)
        header, _ = _call(
            str(sock_path), cap.token, "POST", "127.0.0.1", "/v1/systemone", port=server.server_address[1]
        )
        proc.terminate()
        assert header.get("error") == "upstream"
        assert "gzip" in str(header.get("detail"))
    finally:
        server.shutdown()
        shutil.rmtree(short_root, ignore_errors=True)


def test_the_broker_refuses_to_start_without_a_self_bound_deadline(tmp_path: Path) -> None:
    """F5: `max_lifetime_s` is mandatory — a broker that could outlive its study must not start."""
    import tempfile

    short_root = Path(tempfile.mkdtemp(prefix="jevbrk-", dir="/tmp"))
    try:
        key_file = short_root / "typesafe.key"
        key_file.write_text(KEY + "\n", encoding="utf-8")
        config = {
            "listen": str(short_root / "broker.sock"),
            "grants_dir": str(short_root / "grants"),
            "upstreams": [
                {
                    "name": "typesafe",
                    "scheme": "http",
                    "host": "127.0.0.1",
                    "port": 1,
                    "methods": ["POST"],
                    "paths": ["/v1/systemone"],
                    "credential_file": str(key_file),
                }
            ],
        }
        config_path = short_root / "broker-config.json"
        config_path.write_text(json.dumps(config), encoding="utf-8")
        done = subprocess.run(
            [sys.executable, str(BROKER), str(config_path)], capture_output=True, text=True, timeout=30
        )
        assert done.returncode != 0
        assert "max_lifetime_s" in done.stderr
    finally:
        shutil.rmtree(short_root, ignore_errors=True)


def test_the_broker_log_and_receipts_never_contain_a_credential(stack: Any) -> None:
    cap = mint_capability(stack["grants"], 60)
    _call(stack["sock"], cap.token, "POST", "127.0.0.1", "/v1/systemone", port=stack["port"])
    _call(stack["sock"], "unknown-token-for-log-scan", "POST", "127.0.0.1", "/v1/systemone", port=stack["port"])
    _call(stack["sock"], cap.token, "POST", "evil.example", "/x", port=1)
    deadline = time.time() + 5
    while len(stack["log"].read_text(encoding="utf-8").splitlines()) < 4:
        assert time.time() < deadline, "the broker did not write its receipts"
        time.sleep(0.05)
    log = stack["log"].read_text(encoding="utf-8")
    assert KEY not in log and PROVIDER_KEY not in log
    assert cap.token not in log, "receipts name a capability by hash, never by token"
    assert '"refused":"allowlist"' in log
    assert '"refused":"capability"' in log
