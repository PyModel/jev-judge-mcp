"""A taken HTTP port is retried on a clock the test owns, then refused by name (ADR-0055)."""

import asyncio
import errno
import socket

import pytest

from jev_judge_mcp.server import (
    HTTP_BIND_ATTEMPTS,
    HTTP_BIND_BUDGET_S,
    ensure_http_port_free,
    http_serve_sockets,
)
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


def _time_wait_port(*, listener_sets_reuseaddr: bool) -> int:
    """A loopback port whose only holder is a `TIME_WAIT` entry, not a listener.

    Closing the accepted socket first puts this side in `TIME_WAIT`. No sleep: the entry is there
    as soon as the sockets close, and a bind without `SO_REUSEADDR` is the proof. Linux keeps the
    entry's flag from the listener that made it, so that flag decides whether a new `SO_REUSEADDR`
    bind succeeds; macOS consults only the new socket (ADR-0055).
    """
    listener = socket.socket()
    if listener_sets_reuseaddr:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
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


def _real_bind_free(host: str, port: int) -> bool:
    """Whether uvicorn's bind would succeed right now: asyncio `loop.create_server`, which defaults
    `reuse_address` to true on POSIX, on this host and port. Its server is closed immediately."""

    async def attempt() -> bool:
        loop = asyncio.get_running_loop()
        try:
            server = await loop.create_server(asyncio.Protocol, host=host, port=port)
        except OSError:
            return False
        server.close()
        await server.wait_closed()
        return True

    return asyncio.run(attempt())


def test_a_time_wait_from_a_reuseaddr_listener_is_free(monkeypatch: pytest.MonkeyPatch) -> None:
    """The realistic restart: the previous server set `SO_REUSEADDR`, and so does the probe."""
    port = _time_wait_port(listener_sets_reuseaddr=True)
    bare = socket.socket()
    try:
        with pytest.raises(OSError) as blocked:
            bare.bind(("127.0.0.1", port))
    finally:
        bare.close()
    assert blocked.value.errno == errno.EADDRINUSE
    assert _real_bind_free("127.0.0.1", port)
    clock = Clock()
    ensure_http_port_free(http_settings(monkeypatch, port), sleep=clock.sleep, clock=clock.clock)
    assert clock.sleeps == []


def test_a_time_wait_from_a_bare_listener_agrees_with_the_real_bind(monkeypatch: pytest.MonkeyPatch) -> None:
    """Linux keeps the entry's flag, so a bare `TIME_WAIT` blocks the probe exactly as it blocks
    uvicorn's own bind; macOS frees both. The verdict the process wants is the real bind's."""
    port = _time_wait_port(listener_sets_reuseaddr=False)
    real_free = _real_bind_free("127.0.0.1", port)
    clock = Clock()
    try:
        ensure_http_port_free(http_settings(monkeypatch, port), sleep=clock.sleep, clock=clock.clock)
    except SystemExit as exited:
        probe_free = False
        assert exited.code == f"JEV_MCP_HTTP_PORT={port} is already in use; set JEV_MCP_HTTP_PORT to a free port"
    else:
        probe_free = True
        assert clock.sleeps == []
    assert probe_free == real_free


def test_a_live_listener_is_taken(monkeypatch: pytest.MonkeyPatch) -> None:
    """A socket that is still listening is `EADDRINUSE` for the real bind and the probe alike."""
    holder = socket.socket()
    holder.bind(("127.0.0.1", 0))
    holder.listen(1)
    port: int = holder.getsockname()[1]
    clock = Clock()
    try:
        assert not _real_bind_free("127.0.0.1", port)
        with pytest.raises(SystemExit, match=f"JEV_MCP_HTTP_PORT={port} is already in use") as raised:
            ensure_http_port_free(http_settings(monkeypatch, port), sleep=clock.sleep, clock=clock.clock)
    finally:
        holder.close()
    assert raised.value.code == f"JEV_MCP_HTTP_PORT={port} is already in use; set JEV_MCP_HTTP_PORT to a free port"


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


def test_the_serve_bind_keeps_its_sockets_open(monkeypatch: pytest.MonkeyPatch) -> None:
    """`http_serve_sockets` binds and holds: those sockets are the listener uvicorn is handed."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port: int = probe.getsockname()[1]
    bound = http_serve_sockets(http_settings(monkeypatch, port))
    assert bound is not None  # 127.0.0.1 resolves; a non-resolving host returns None
    try:
        assert len(bound) >= 1
        assert all(sock.getsockname()[1] == port for sock in bound)
    finally:
        for sock in bound:
            sock.close()


def test_a_port_taken_after_the_gate_refuses_with_the_one_line(monkeypatch: pytest.MonkeyPatch) -> None:
    """The gap between the startup probe and the listen is closed: the serve bind itself refuses."""
    holder = socket.socket()
    holder.bind(("127.0.0.1", 0))
    holder.listen(1)
    port: int = holder.getsockname()[1]
    try:
        with pytest.raises(SystemExit, match=f"JEV_MCP_HTTP_PORT={port} is already in use"):
            http_serve_sockets(http_settings(monkeypatch, port))
    finally:
        holder.close()


def test_serve_hands_uvicorn_the_prebound_sockets(monkeypatch: pytest.MonkeyPatch) -> None:
    """The wiring J7 claims: uvicorn listens on the sockets this process bound, never its own bind.

    Dropping `sockets=` from the serve call would reopen the probe's TOCTOU gap and pass every
    other layer, so this records what `serve` was actually handed: the pre-bound sockets for
    streamable-http, and no serve call at all for stdio.
    """
    import anyio

    from jev_judge_mcp import server as server_module
    from jev_judge_mcp.server import JevMCPServer, build_server

    serve_call = server_module._serve  # pyright: ignore[reportPrivateUsage]
    uvicorn_server = server_module._UvicornServer  # pyright: ignore[reportPrivateUsage]
    handed: list[list[socket.socket] | None] = []
    listened: list[int] = []

    async def fake_serve(self: object, sockets: list[socket.socket] | None = None) -> None:
        del self
        handed.append(sockets)
        for sock in sockets or []:
            listened.append(sock.getsockname()[1])
            sock.close()

    async def fake_stdio(self: object) -> None:
        del self
        return None

    monkeypatch.setattr(uvicorn_server, "serve", fake_serve)
    monkeypatch.setattr(JevMCPServer, "run_stdio_async", fake_stdio)

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port: int = probe.getsockname()[1]
    anyio.run(serve_call, build_server(load_settings()), http_settings(monkeypatch, port))
    assert handed[-1] is not None
    assert listened == [port]

    monkeypatch.delenv("JEV_MCP_TRANSPORT", raising=False)
    stdio_settings = load_settings()
    assert http_serve_sockets(stdio_settings) is None
    anyio.run(serve_call, build_server(stdio_settings), stdio_settings)
    assert len(handed) == 1  # stdio never asks uvicorn to serve
