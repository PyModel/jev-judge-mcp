"""Optional Streamable HTTP transport (`JEV_MCP_TRANSPORT=streamable-http`)."""

import json
import signal
import socket
import time

import httpx
import pytest

from tests.support.stdio import INITIALIZE, PROTOCOL_VERSION, StdioServer


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port: int = sock.getsockname()[1]
        return port


def post_initialize(url: str) -> dict[str, object]:
    deadline = time.monotonic() + 15
    while True:
        try:
            response = httpx.post(url, json=INITIALIZE, headers={"Accept": "application/json, text/event-stream"})
            break
        except httpx.ConnectError:
            if time.monotonic() > deadline:
                raise
            time.sleep(0.1)
    response.raise_for_status()
    data = next(line[len("data: ") :] for line in response.text.splitlines() if line.startswith("data: "))
    message: dict[str, object] = json.loads(data)
    return message


@pytest.mark.parametrize("signum", [signal.SIGINT, signal.SIGTERM], ids=["SIGINT", "SIGTERM"])
def test_http_initialize_and_clean_shutdown(signum: signal.Signals) -> None:
    port = free_port()
    env = {"JEV_MCP_TRANSPORT": "streamable-http", "JEV_MCP_HTTP_PORT": str(port)}
    with StdioServer(env=env) as server:
        reply = post_initialize(f"http://127.0.0.1:{port}/mcp")
        server.process.send_signal(signum)
        returncode, stderr = server.wait()
        stdout = b"".join(server.stdout_lines)
    result = reply["result"]
    assert isinstance(result, dict)
    assert result["protocolVersion"] == PROTOCOL_VERSION  # pyright: ignore[reportUnknownMemberType]
    assert result["serverInfo"]["name"] == "jev-mcp"  # pyright: ignore[reportUnknownMemberType, reportIndexIssue]
    assert returncode == 0
    assert "Traceback" not in stderr
    assert stdout == b""


def test_http_lone_surrogate_escape_is_refused_with_a_reply() -> None:
    """Streamable HTTP keeps the SDK's parser, which rejects `\\ud83d`; the POST is answered with -32700,
    not dropped (divergence `lone-surrogate-http-parse-error`; stdio serves it as the reference does)."""
    port = free_port()
    url = f"http://127.0.0.1:{port}/mcp"
    headers = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
    with StdioServer(env={"JEV_MCP_TRANSPORT": "streamable-http", "JEV_MCP_HTTP_PORT": str(port)}) as server:
        post_initialize(url)
        frame = '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"x\\ud83dy","arguments":{}}}'
        response = httpx.post(url, content=frame.encode(), headers=headers)
        server.process.send_signal(signal.SIGTERM)
        returncode, _ = server.wait()
    reply = response.json()
    assert reply["id"] is None
    assert reply["error"]["code"] == -32700
    assert reply["error"]["message"].startswith("Parse error: unexpected end of hex escape")
    assert returncode == 0
