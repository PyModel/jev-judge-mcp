"""The spawned server under hostile traffic (ROADMAP P6): 64 concurrent calls with cancellation,
broken provider connections, credential reflection, stdout contamination, and lone surrogates.

The provider is `tests/support/peer.py` on 127.0.0.1; nothing leaves the host.
"""

import json
import re
import signal
import time
from typing import Any

import pytest

from tests.security.tools import CASES
from tests.support.peer import Peer
from tests.support.secrets import secret_env
from tests.support.stdio import StdioServer

CONCURRENT = 64
CANCELLED = 8


def call(request_id: int, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    }


def cancel(request_id: int) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": request_id}}


def screen(request_id: int, text: str) -> dict[str, Any]:
    return call(request_id, "jev_screen", {"text": text})


def text_of(reply: dict[str, Any]) -> str:
    return reply["result"]["content"][0]["text"]


def replies_until(server: StdioServer, ids: set[int]) -> dict[int, dict[str, Any]]:
    """Every reply until all of `ids` have one; notifications are skipped."""
    replies: dict[int, dict[str, Any]] = {}
    while not ids <= replies.keys():
        reply = server.receive()
        if "id" in reply:
            replies[reply["id"]] = reply
    return replies


def assert_protocol_only(lines: list[bytes]) -> None:
    """Every stdout line is one complete JSON-RPC 2.0 frame: a response or a notification."""
    for line in lines:
        assert line.endswith(b"\n"), line
        frame = json.loads(line)
        assert frame["jsonrpc"] == "2.0", line
        assert ("id" in frame and ("result" in frame or "error" in frame)) or "method" in frame, line


def assert_tracebacks_are_logged_tool_failures(stderr: str) -> None:
    """Each traceback is the `tool <name> raised` log. Any other traceback is an uncaught crash."""
    marker = "Traceback (most recent call last):"
    for preamble in stderr.split(marker)[:-1]:
        lines = preamble.rstrip().splitlines()
        assert lines, "traceback with no tool log line"
        line = lines[-1]
        assert line.endswith(" raised"), line
        assert " tool " in line, line


def wait_for(condition: Any, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while not condition():
        assert time.monotonic() < deadline, "condition not met in time"
        time.sleep(0.02)


def test_64_concurrent_calls_are_isolated_and_cancellation_kills_only_its_own() -> None:
    """Each reply carries its own call's marker; a cancelled call gets no reply and its provider
    request is aborted (ADR-0011), while every other in-flight call completes."""
    echo_ids = range(100, 100 + CONCURRENT - CANCELLED)
    hang_ids = range(500, 500 + CANCELLED)
    with Peer(delay=0.3) as peer, StdioServer(env=peer.env()) as server:
        server.initialize()
        for index, request_id in enumerate(echo_ids):
            server.send(screen(request_id, f"echo:{index + 1}"))
            if index < CANCELLED:
                server.send(screen(hang_ids[index], f"hang:{hang_ids[index]}"))
        wait_for(lambda: len(peer.hanging) == CANCELLED)
        for request_id in hang_ids:
            server.send(cancel(request_id))
        replies = replies_until(server, set(echo_ids))
        wait_for(lambda: len(peer.aborted) == CANCELLED)
        ping = server.request({"jsonrpc": "2.0", "id": 999, "method": "ping"})
        server.close_stdin()
        returncode, _ = server.wait()
        lines = server.stdout_lines

    for index, request_id in enumerate(echo_ids):
        payload = json.loads(text_of(replies[request_id]))
        assert payload["probabilities"]["injection"] == (index + 1) / 1000, request_id
    assert peer.aborted == {str(request_id) for request_id in hang_ids}
    answered = {json.loads(line).get("id") for line in lines}
    assert answered.isdisjoint(hang_ids)
    assert ping["result"] == {}
    assert returncode == 0
    assert_protocol_only(lines)


def test_cancellation_is_handled_without_a_missing_handler_log() -> None:
    """`notifications/cancelled` (and `initialized`) have handlers: at DEBUG nothing reports them unhandled,
    and the cancelled call still gets no reply while its provider request is aborted."""
    with Peer() as peer, StdioServer(env={**peer.env(), "JEV_MCP_LOG_LEVEL": "DEBUG"}) as server:
        server.initialize()
        server.send(screen(10, "hang:10"))
        wait_for(lambda: "10" in peer.hanging)
        server.send(cancel(10))
        wait_for(lambda: "10" in peer.aborted)
        ping = server.request({"jsonrpc": "2.0", "id": 11, "method": "ping"})
        server.close_stdin()
        returncode, stderr = server.wait()
        lines = server.stdout_lines
    assert ping["result"] == {}
    assert 10 not in {json.loads(line).get("id") for line in lines}
    assert "no handler for notification" not in stderr, stderr
    assert returncode == 0


def test_broken_connections_fail_the_call_not_the_server() -> None:
    with Peer() as peer, StdioServer(env=peer.env()) as server:
        server.initialize()
        replies = {
            kind: server.request(screen(request_id, kind))
            for request_id, kind in enumerate(("reset", "garbage", "echo:5"), start=10)
        }
        server.close_stdin()
        returncode, stderr = server.wait()
        lines = server.stdout_lines

    for kind in ("reset", "garbage"):
        assert replies[kind]["result"]["isError"] is True
        assert text_of(replies[kind]).startswith("Jev-compatible endpoint request failed:"), kind
    assert json.loads(text_of(replies["echo:5"]))["probabilities"]["injection"] == 0.005
    assert_tracebacks_are_logged_tool_failures(stderr)
    for kind in ("reset", "garbage", "echo:5"):
        assert kind not in stderr
    assert returncode == 0
    assert_protocol_only(lines)


@pytest.mark.parametrize(
    ("literal", "parses"), [("1e400", True), ("-1e400", True), ("NaN", False), ("Infinity", False)]
)
def test_non_finite_answers_on_the_wire_fail_closed(literal: str, parses: bool) -> None:
    """`1e400` parses to infinity and fails validation; `NaN`/`Infinity` are not JSON at all. Neither passes."""
    with Peer() as peer, StdioServer(env=peer.env()) as server:
        server.initialize()
        reply = server.request(screen(10, f"number:{literal}"))
        server.close_stdin()
        server.wait()
    text = text_of(reply)
    assert reply["result"].get("isError", False) is not parses, text
    if not parses:
        assert text.startswith("Jev-compatible endpoint returned an invalid response"), text
    else:
        payload = json.loads(text)
        assert payload["status"] == "invalid_response"
        assert payload["recommendation"]["action"] == "review"


def secret_needles(env: dict[str, str]) -> set[str]:
    """What must never be seen: every secret value, and each URL secret's userinfo and password."""
    needles = set(env.values())
    for value in env.values():
        needles.update(re.findall(r"marker-[a-z0-9-]+", value))
        if match := re.match(r"\w+://([^@/]+)@", value):
            needles.add(match.group(1))
    return needles


def secrets_of(env: dict[str, str]) -> dict[str, str]:
    """The schema-secret variables of a server environment (ADR-0017)."""
    return {name: value for name, value in env.items() if name in secret_env()}


def assert_nothing_leaked(env: dict[str, str], stdout: bytes, stderr: str) -> None:
    text = stdout.decode()
    for needle in secret_needles(secrets_of(env)):
        assert needle not in text, needle
        assert needle not in stderr, needle


def provider_env(provider: str, base_url: str) -> dict[str, str]:
    """Every schema secret set to its marker (ADR-0017), the chosen provider's base URL at `base_url`."""
    variable = {"compatible": "JEV_API_BASE_URL", "typesafe": "TYPESAFE_BASE_URL"}[provider]
    return {**secret_env(), variable: base_url, "JEV_PROVIDER": provider, "JEV_MCP_LOG_LEVEL": "DEBUG"}


@pytest.mark.parametrize("provider", ["compatible", "typesafe"])
def test_reflected_credentials_never_reach_stdout_or_stderr(provider: str) -> None:
    """A 401 whose body echoes a configured secret (one per call, each before the 200-unit cut) and
    the Authorization header: every tool result, log line (at DEBUG), and frame is redacted."""
    with Peer(reflect_all=provider == "typesafe") as peer:
        env = provider_env(provider, peer.url)
        peer.reflect = tuple(sorted(secret_needles(secrets_of(env))))
        with StdioServer(env=env) as server:
            server.initialize()
            replies = [server.request(screen(10 + n, "reflect")) for n in range(len(peer.reflect))]
            server.close_stdin()
            returncode, stderr = server.wait()
            stdout = b"".join(server.stdout_lines)
    assert peer.requests >= len(peer.reflect)
    for reply in replies:
        assert reply["result"]["isError"] is True
        assert "[redacted]" in text_of(reply)
    assert returncode == 0
    assert_nothing_leaked(env, stdout, stderr)


@pytest.mark.parametrize("provider", ["compatible", "typesafe"])
def test_base_url_with_userinfo_is_refused_before_any_request(provider: str) -> None:
    """ADR-0008: userinfo in a base URL is a credential; the request is never built, nothing leaks."""
    with Peer() as peer:
        host = peer.url.removeprefix("http://")
        env = provider_env(provider, f"http://user:marker-userinfo-{provider}@{host}")
        with StdioServer(env=env) as server:
            server.initialize()
            reply = server.request(screen(10, "echo:1"))
            server.close_stdin()
            returncode, stderr = server.wait()
            stdout = b"".join(server.stdout_lines)
    assert peer.requests == 0
    assert reply["result"]["isError"] is True
    assert "Request cannot be constructed from a URL that includes credentials" in text_of(reply)
    assert returncode == 0
    assert_nothing_leaked(env, stdout, stderr)


def test_stdout_is_protocol_frames_under_every_hostile_input() -> None:
    """Every tool, worker processes timing out, malformed frames, oversized and control-character
    arguments, unknown tools, cancellation, and a signal: stdout still carries frames only."""
    hostile = "\x00\x1b[2J\r\nContent-Length: 0\r\n\r\n" + "\u202e" * 10 + "😀\ud83d" + "x" * 1_000_000
    with Peer(delay=0.2) as peer, StdioServer(env={**peer.env(), "PYTHONWARNINGS": "always"}) as server:
        server.initialize()
        request_id = 10
        for case in CASES:
            server.send(call(request_id, case.tool, dict(case.arguments)))
            request_id += 1
        server.send(
            call(
                request_id,
                "jev_extract",
                {
                    "document": "a" * 40 + "!",
                    "fields": [{"id": "slow", "pattern": "(a|aa)+$", "description": "Pathological."}],
                },
            )
        )
        server.send(screen(request_id + 1, hostile))
        server.send(call(request_id + 2, "no_such_tool", {"text": hostile}))
        server.send(screen(request_id + 3, "hang:x"))
        wait_for(lambda: "x" in peer.hanging)
        server.send(cancel(request_id + 3))
        assert server.process.stdin is not None
        for frame in (b"not json\n", b'{"jsonrpc": "2.0", "id": 77, "method"\n', b"\xff\xfe\n", b"[]\n"):
            server.process.stdin.write(frame)
        server.process.stdin.flush()
        replies_until(server, set(range(10, request_id + 3)))
        server.request({"jsonrpc": "2.0", "id": 999, "method": "ping"})
        server.process.send_signal(signal.SIGTERM)
        returncode, stderr = server.wait()
        lines = server.stdout_lines
    assert returncode == 0
    assert_tracebacks_are_logged_tool_failures(stderr)
    assert hostile not in stderr
    assert_protocol_only(lines)


def test_lone_surrogate_escapes_are_served_like_the_reference() -> None:
    """`JSON.parse` accepts `\\ud83d` and `JSON.stringify` writes it back
    as that escape. The call runs on the text as sent, an echo of it stays escaped on the wire, and a
    lone-surrogate id is answered. Raw CESU-8 surrogate bytes become U+FFFD on read, as Node's
    `Buffer.toString('utf8')` makes them in the reference, and that call runs too.
    """
    frames = [
        '{"jsonrpc":"2.0","id":7,"method":"tools/call","params":{"name":"jev_screen","arguments":{"text":"x\\ud83dy"}}}',
        '{"jsonrpc":"2.0","id":8,"method":"tools/call","params":{"name":"x\\ud83dy","arguments":{}}}',
        '{"jsonrpc":"2.0","id":"a\\udc00","method":"ping"}',
    ]
    cesu = (
        b'{"jsonrpc":"2.0","id":9,"method":"tools/call",'
        b'"params":{"name":"jev_screen","arguments":{"text":"\xed\xa0\xbdecho:9"}}}'
    )
    with Peer() as peer, StdioServer(env={**peer.env(), "JEV_MCP_LOG_LEVEL": "DEBUG"}) as server:
        server.initialize()
        assert server.process.stdin is not None
        server.process.stdin.write("\n".join(frames).encode() + b"\n" + cesu + b"\n")
        server.process.stdin.flush()
        wait_for(lambda: len(server.stdout_lines) >= 5)  # initialize and the four replies, in any order
        server.close_stdin()
        returncode, stderr = server.wait()
        lines = server.stdout_lines
    frames_by_id = {json.loads(line).get("id"): json.loads(line) for line in lines}
    assert sorted(frames_by_id, key=str) == [1, 7, 8, 9, "a\udc00"]
    assert frames_by_id[7]["result"]["isError"] is True  # the peer saw no `echo:` command, only the text
    assert text_of(frames_by_id[8]) == "MCP error -32602: Tool x\ud83dy not found"
    assert b'"MCP error -32602: Tool x\\ud83dy not found"' in b"".join(lines)  # escaped, as JSON.stringify
    assert frames_by_id["a\udc00"]["result"] == {}
    assert frames_by_id[9]["result"]["isError"] is True  # nor here: the text leads with U+FFFD
    assert sorted(peer.contents) == ["x\ud83dy", "\ufffd\ufffd\ufffdecho:9"]
    assert_tracebacks_are_logged_tool_failures(stderr)
    assert "x\ud83dy" not in stderr
    assert "echo:9" not in stderr
    assert returncode == 0
    assert_protocol_only(lines)
