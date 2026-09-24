"""The HTTP access-control gate and bearer token, end to end on real sockets (ADR-0050).

Local binds use only `127.0.0.1`. One CI-only test binds `0.0.0.0` with a token; refusal cases exit
before binding, so an unauthenticated non-loopback host only reaches the startup gate.
"""

import json
import os
import signal
import socket
import time

import httpx
import pytest

from tests.support.stdio import INITIALIZE, PROTOCOL_VERSION, StdioServer

TOKEN = "integration-token-" + "x" * 32


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port: int = sock.getsockname()[1]
        return port


def post(url: str, payload: object, headers: dict[str, str] | None = None) -> httpx.Response:
    """POST with the connect retry `test_http.py` uses; headers win over the defaults."""
    merged = {"Accept": "application/json, text/event-stream", **(headers or {})}
    deadline = time.monotonic() + 15
    while True:
        try:
            return httpx.post(url, json=payload, headers=merged)
        except httpx.ConnectError:
            if time.monotonic() > deadline:
                raise
            time.sleep(0.1)


def test_non_loopback_bind_without_a_token_refuses_to_start() -> None:
    env = {"JEV_MCP_TRANSPORT": "streamable-http", "JEV_MCP_HTTP_HOST": "192.0.2.1"}
    with StdioServer(env=env) as server:
        returncode, stderr = server.wait()
    assert returncode != 0
    assert "JEV_MCP_HTTP_TOKEN" in stderr
    assert "Traceback" not in stderr


def test_a_short_token_refuses_to_start_without_echoing_itself() -> None:
    env = {
        "JEV_MCP_TRANSPORT": "streamable-http",
        "JEV_MCP_HTTP_HOST": "127.0.0.1",
        "JEV_MCP_HTTP_TOKEN": "short-token",
    }
    with StdioServer(env=env) as server:
        returncode, stderr = server.wait()
    assert returncode != 0
    assert "JEV_MCP_HTTP_TOKEN" in stderr
    assert "short-token" not in stderr


def test_the_token_is_required_and_admits_initialize() -> None:
    port = free_port()
    url = f"http://127.0.0.1:{port}/mcp"
    env = {
        "JEV_MCP_TRANSPORT": "streamable-http",
        "JEV_MCP_HTTP_PORT": str(port),
        "JEV_MCP_HTTP_TOKEN": TOKEN,
    }
    with StdioServer(env=env) as server:
        missing = post(url, INITIALIZE)
        assert missing.status_code == 401
        assert missing.headers["www-authenticate"] == "Bearer"
        assert TOKEN not in missing.text

        wrong = post(url, INITIALIZE, headers={"Authorization": f"Bearer {'w' * 48}"})
        assert wrong.status_code == 401
        assert TOKEN not in wrong.text

        correct = post(url, INITIALIZE, headers={"Authorization": f"Bearer {TOKEN}"})
        assert correct.status_code == 200
        data = next(line[len("data: ") :] for line in correct.text.splitlines() if line.startswith("data: "))
        result = json.loads(data)["result"]
        assert result["protocolVersion"] == PROTOCOL_VERSION
        assert result["serverInfo"]["name"] == "jev-mcp"

        server.process.send_signal(signal.SIGTERM)
        returncode, stderr = server.wait()
    assert returncode == 0
    assert "Traceback" not in stderr
    # The token joins redaction (ADR-0017) and is printed nowhere, on the success path or the 401s.
    assert TOKEN not in stderr


@pytest.mark.skipif(not os.getenv("CI"), reason="binding beyond loopback is CI-only")
def test_non_loopback_bind_with_token_authenticates_requests() -> None:
    port = free_port()
    url = f"http://127.0.0.1:{port}/mcp"
    env = {
        "JEV_MCP_TRANSPORT": "streamable-http",
        "JEV_MCP_HTTP_HOST": "0.0.0.0",
        "JEV_MCP_HTTP_PORT": str(port),
        "JEV_MCP_HTTP_TOKEN": TOKEN,
    }
    with StdioServer(env=env) as server:
        missing = post(url, INITIALIZE)
        assert missing.status_code == 401
        assert missing.headers["www-authenticate"] == "Bearer"
        assert TOKEN not in missing.text

        authenticated = post(url, INITIALIZE, headers={"Authorization": f"Bearer {TOKEN}"})
        assert authenticated.status_code == 200
        data = next(line[len("data: ") :] for line in authenticated.text.splitlines() if line.startswith("data: "))
        assert json.loads(data)["result"]["serverInfo"]["name"] == "jev-mcp"
        server.process.send_signal(signal.SIGTERM)
        returncode, stderr = server.wait()
    assert returncode == 0
    assert "Traceback" not in stderr
    assert TOKEN not in stderr


def test_loopback_default_still_rejects_a_forged_host() -> None:
    port = free_port()
    url = f"http://127.0.0.1:{port}/mcp"
    env = {"JEV_MCP_TRANSPORT": "streamable-http", "JEV_MCP_HTTP_PORT": str(port)}
    with StdioServer(env=env) as server:
        forged = post(url, INITIALIZE, headers={"Host": "evil.example", "Origin": "http://evil.example"})
        assert forged.status_code == 421
        normal = post(url, INITIALIZE)
        assert normal.status_code == 200
        server.process.send_signal(signal.SIGTERM)
        returncode, stderr = server.wait()
    assert returncode == 0
    assert "Traceback" not in stderr


def test_a_short_configured_secret_refuses_startup_naming_the_variable() -> None:
    env = {"TYPESAFE_API_KEY": "abc"}
    with StdioServer(env=env) as server:
        returncode, stderr = server.wait()
    assert returncode != 0
    assert "TYPESAFE_API_KEY" in stderr
    assert "abc" not in stderr
    assert "Traceback" not in stderr
