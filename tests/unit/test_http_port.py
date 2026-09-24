"""A taken HTTP port is retried on a clock the test owns, then refused by name (ADR-0055)."""

import errno
import socket

import pytest

from jev_judge_mcp.server import HTTP_BIND_ATTEMPTS, HTTP_BIND_BUDGET_S, ensure_http_port_free
from jev_judge_mcp.settings import Settings, load_settings


class Clock:
    """Advances only when the code under test sleeps, so the budget is not a wall-clock wait."""

    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def http_settings(monkeypatch: pytest.MonkeyPatch, port: int) -> Settings:
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    monkeypatch.setenv("JEV_MCP_TRANSPORT", "streamable-http")
    monkeypatch.setenv("JEV_MCP_HTTP_HOST", "127.0.0.1")
    monkeypatch.setenv("JEV_MCP_HTTP_PORT", str(port))
    return load_settings()


def test_stdio_does_not_bind(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("JEV_MCP_TRANSPORT", raising=False)
    clock = Clock()
    ensure_http_port_free(load_settings(), sleep=clock.sleep, clock=clock.clock)
    assert clock.sleeps == []


def test_a_free_port_returns_without_waiting(monkeypatch: pytest.MonkeyPatch) -> None:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port: int = probe.getsockname()[1]
    clock = Clock()
    ensure_http_port_free(http_settings(monkeypatch, port), sleep=clock.sleep, clock=clock.clock)
    assert clock.sleeps == []


def test_a_taken_port_retries_inside_the_budget_then_names_that_port(monkeypatch: pytest.MonkeyPatch) -> None:
    holder = socket.socket()
    holder.bind(("127.0.0.1", 0))
    holder.listen(1)
    port: int = holder.getsockname()[1]
    clock = Clock()
    try:
        with pytest.raises(SystemExit, match=f"JEV_MCP_HTTP_PORT={port} is already in use") as raised:
            ensure_http_port_free(http_settings(monkeypatch, port), sleep=clock.sleep, clock=clock.clock)
    finally:
        holder.close()
    assert raised.value.code == f"JEV_MCP_HTTP_PORT={port} is already in use; set JEV_MCP_HTTP_PORT to a free port"
    assert len(clock.sleeps) == HTTP_BIND_ATTEMPTS - 1
    assert sum(clock.sleeps) <= HTTP_BIND_BUDGET_S
    assert "8088" not in str(raised.value.code)
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", port))


def test_a_port_that_frees_during_the_budget_is_the_same_port(monkeypatch: pytest.MonkeyPatch) -> None:
    holder = socket.socket()
    holder.bind(("127.0.0.1", 0))
    holder.listen(1)
    port: int = holder.getsockname()[1]
    clock = Clock()

    def sleep(seconds: float) -> None:
        clock.sleep(seconds)
        holder.close()

    ensure_http_port_free(http_settings(monkeypatch, port), sleep=sleep, clock=clock.clock)
    assert clock.sleeps == [HTTP_BIND_BUDGET_S / (HTTP_BIND_ATTEMPTS - 1)]
    assert http_settings(monkeypatch, port).http_port == port


def test_another_bind_error_fails_on_the_first_try(monkeypatch: pytest.MonkeyPatch) -> None:
    def denied(self: socket.socket, address: object) -> None:
        raise OSError(errno.EACCES, "denied")

    monkeypatch.setattr(socket.socket, "bind", denied)
    clock = Clock()
    with pytest.raises(OSError) as raised:
        ensure_http_port_free(http_settings(monkeypatch, 8088), sleep=clock.sleep, clock=clock.clock)
    assert raised.value.errno == errno.EACCES
    assert clock.sleeps == []


def _time_wait_port() -> int:
    """A loopback port whose only holder is a `TIME_WAIT` entry, not a listener.

    Closing the accepted socket first puts this side in `TIME_WAIT`. No sleep: the entry is there
    as soon as the sockets close, and a bind without `SO_REUSEADDR` is the proof.
    """
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port: int = listener.getsockname()[1]
    client = socket.socket()
    client.connect(("127.0.0.1", port))
    accepted, _ = listener.accept()
    accepted.close()
    client.close()
    listener.close()
    return port


def test_time_wait_is_not_taken_and_a_live_listener_is(monkeypatch: pytest.MonkeyPatch) -> None:
    """`SO_REUSEADDR` matches uvicorn: `TIME_WAIT` is free, a listener is not (ADR-0055)."""
    port = _time_wait_port()
    bare = socket.socket()
    try:
        with pytest.raises(OSError) as blocked:
            bare.bind(("127.0.0.1", port))
    finally:
        bare.close()
    assert blocked.value.errno == errno.EADDRINUSE
    clock = Clock()
    ensure_http_port_free(http_settings(monkeypatch, port), sleep=clock.sleep, clock=clock.clock)
    assert clock.sleeps == []

    holder = socket.socket()
    holder.bind(("127.0.0.1", 0))
    holder.listen(1)
    live: int = holder.getsockname()[1]
    clock = Clock()
    try:
        with pytest.raises(SystemExit, match=f"JEV_MCP_HTTP_PORT={live} is already in use") as raised:
            ensure_http_port_free(http_settings(monkeypatch, live), sleep=clock.sleep, clock=clock.clock)
    finally:
        holder.close()
    assert raised.value.code == f"JEV_MCP_HTTP_PORT={live} is already in use; set JEV_MCP_HTTP_PORT to a free port"


def test_an_exhausted_budget_does_not_keep_retrying(monkeypatch: pytest.MonkeyPatch) -> None:
    """The deadline is read again after a failed bind, so a jumped clock skips the wait."""
    holder = socket.socket()
    holder.bind(("127.0.0.1", 0))
    holder.listen(1)
    port: int = holder.getsockname()[1]
    clock = Clock()
    reads = 0

    def jumped() -> float:
        nonlocal reads
        reads += 1
        # The first read sets the deadline. The next, after the bind fails, is already past it.
        return 0.0 if reads == 1 else HTTP_BIND_BUDGET_S

    try:
        with pytest.raises(SystemExit, match=f"JEV_MCP_HTTP_PORT={port} is already in use"):
            ensure_http_port_free(http_settings(monkeypatch, port), sleep=clock.sleep, clock=jumped)
    finally:
        holder.close()
    assert clock.sleeps == []
