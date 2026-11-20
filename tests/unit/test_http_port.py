"""A taken HTTP port is retried on a clock the test owns, then refused by name (ADR-0055)."""

import asyncio
import errno
import select
import socket
import sys

import pytest

from jev_judge_mcp.server import (
    HTTP_BIND_ATTEMPTS,
    HTTP_BIND_BUDGET_S,
    http_serve_sockets,
)
from jev_judge_mcp.settings import Settings, load_settings

WILDCARD = "0.0.0.0"  # noqa: S104 — exercise wildcard binds on ephemeral ports


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


def http_settings(monkeypatch: pytest.MonkeyPatch, port: int, host: str = "127.0.0.1") -> Settings:
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    monkeypatch.setenv("JEV_MCP_TRANSPORT", "streamable-http")
    monkeypatch.setenv("JEV_MCP_HTTP_HOST", host)
    monkeypatch.setenv("JEV_MCP_HTTP_PORT", str(port))
    monkeypatch.setenv("JEV_MCP_HTTP_TOKEN", "fixture-bearer-token-for-http-transport")
    return load_settings()


def test_stdio_does_not_bind(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("JEV_MCP_TRANSPORT", raising=False)
    clock = Clock()
    assert http_serve_sockets(load_settings(), sleep=clock.sleep, clock=clock.clock) is None
    assert clock.sleeps == []


def test_a_free_port_returns_without_waiting(monkeypatch: pytest.MonkeyPatch) -> None:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port: int = probe.getsockname()[1]
    clock = Clock()
    bound = http_serve_sockets(http_settings(monkeypatch, port), sleep=clock.sleep, clock=clock.clock)
    assert bound is not None
    for sock in bound:
        with sock:
            assert sock.getsockname()[1] == port
    assert clock.sleeps == []


def test_a_taken_port_retries_inside_the_budget_then_names_that_port(monkeypatch: pytest.MonkeyPatch) -> None:
    holder = socket.socket()
    holder.bind(("127.0.0.1", 0))
    holder.listen(1)
    port: int = holder.getsockname()[1]
    clock = Clock()
    try:
        with pytest.raises(SystemExit, match=f"JEV_MCP_HTTP_PORT={port} is already in use") as raised:
            http_serve_sockets(http_settings(monkeypatch, port), sleep=clock.sleep, clock=clock.clock)
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

    bound = http_serve_sockets(http_settings(monkeypatch, port), sleep=sleep, clock=clock.clock)
    assert bound is not None
    for sock in bound:
        with sock:
            assert sock.getsockname()[1] == port
    assert clock.sleeps == [HTTP_BIND_BUDGET_S / (HTTP_BIND_ATTEMPTS - 1)]
    assert http_settings(monkeypatch, port).http_port == port


def test_another_bind_error_fails_on_the_first_try(monkeypatch: pytest.MonkeyPatch) -> None:
    def denied(self: socket.socket, address: object) -> None:
        raise OSError(errno.EACCES, "denied")

    def refused(self: socket.socket, address: object) -> int:
        return errno.ECONNREFUSED

    monkeypatch.setattr(socket.socket, "bind", denied)
    monkeypatch.setattr(socket.socket, "connect_ex", refused)
    clock = Clock()
    with pytest.raises(OSError) as raised:
        http_serve_sockets(http_settings(monkeypatch, 8088), sleep=clock.sleep, clock=clock.clock)
    assert raised.value.errno == errno.EACCES
    assert clock.sleeps == []


def _time_wait_port(*, listener_sets_reuseaddr: bool) -> int:
    """A loopback port whose only holder is a `TIME_WAIT` entry, not a listener.

    Closing the accepted socket first puts this side in `TIME_WAIT`. No sleep: the entry is there
    as soon as the sockets close, and a bind without `SO_REUSEADDR` is the proof. Linux keeps the
    entry's flag from the listener that made it, so that flag decides whether a new `SO_REUSEADDR`
    bind succeeds; macOS consults only the new socket (ADR-0055).
    """
    with socket.socket() as listener, socket.socket() as client:
        if listener_sets_reuseaddr:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        port: int = listener.getsockname()[1]
        client.settimeout(2)
        client.connect(("127.0.0.1", port))
        accepted, _ = listener.accept()
        accepted.close()
        assert client.recv(1) == b""
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


@pytest.mark.parametrize("listener_sets_reuseaddr", [True, False])
def test_a_time_wait_port_follows_the_platform_reuse_policy(
    monkeypatch: pytest.MonkeyPatch, listener_sets_reuseaddr: bool
) -> None:
    """A prior HTTP listener leaves a reusable TIME_WAIT port on POSIX."""
    port = _time_wait_port(listener_sets_reuseaddr=listener_sets_reuseaddr)
    with socket.socket() as bare:
        try:
            bare.bind(("127.0.0.1", port))
        except OSError as blocked:
            assert blocked.errno == errno.EADDRINUSE
        else:
            if sys.platform == "darwin" or sys.platform.startswith("linux"):
                pytest.fail("server-side TIME_WAIT was not observable on a supported platform")
            pytest.skip("this platform did not retain a server-side TIME_WAIT port")
    clock = Clock()
    settings = http_settings(monkeypatch, port)
    if listener_sets_reuseaddr or sys.platform == "darwin":
        bound = http_serve_sockets(settings, sleep=clock.sleep, clock=clock.clock)
        assert bound is not None
        for sock in bound:
            sock.close()
        assert clock.sleeps == []
    else:
        with pytest.raises(SystemExit, match=f"JEV_MCP_HTTP_PORT={port} is already in use"):
            http_serve_sockets(settings, sleep=clock.sleep, clock=clock.clock)
        assert len(clock.sleeps) == HTTP_BIND_ATTEMPTS - 1


def test_a_live_listener_is_taken(monkeypatch: pytest.MonkeyPatch) -> None:
    """A socket already listening prevents startup."""
    holder = socket.socket()
    holder.bind(("127.0.0.1", 0))
    holder.listen(1)
    port: int = holder.getsockname()[1]
    clock = Clock()
    try:
        assert not _real_bind_free("127.0.0.1", port)
        with pytest.raises(SystemExit, match=f"JEV_MCP_HTTP_PORT={port} is already in use") as raised:
            http_serve_sockets(http_settings(monkeypatch, port), sleep=clock.sleep, clock=clock.clock)
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
            http_serve_sockets(http_settings(monkeypatch, port), sleep=clock.sleep, clock=jumped)
    finally:
        holder.close()
    assert clock.sleeps == []


def test_the_serve_bind_keeps_its_sockets_open(monkeypatch: pytest.MonkeyPatch) -> None:
    """`http_serve_sockets` binds and holds: those sockets are the listener uvicorn is handed."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port: int = probe.getsockname()[1]
    bound = http_serve_sockets(http_settings(monkeypatch, port))
    assert bound is not None
    try:
        assert len(bound) >= 1
        assert all(sock.getsockname()[1] == port for sock in bound)
    finally:
        for sock in bound:
            sock.close()


@pytest.mark.parametrize(("held_host", "requested_host"), [(WILDCARD, "127.0.0.1"), ("127.0.0.1", WILDCARD)])
def test_a_wildcard_listener_blocks_the_loopback_bind(
    monkeypatch: pytest.MonkeyPatch, held_host: str, requested_host: str
) -> None:
    clock = Clock()
    with socket.socket() as holder:
        holder.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        holder.bind((held_host, 0))
        holder.listen(1)
        port: int = holder.getsockname()[1]
        with pytest.raises(SystemExit) as raised:
            http_serve_sockets(http_settings(monkeypatch, port, requested_host), sleep=clock.sleep, clock=clock.clock)
    assert raised.value.code == f"JEV_MCP_HTTP_PORT={port} is already in use; set JEV_MCP_HTTP_PORT to a free port"


@pytest.mark.parametrize(
    ("requested_host", "connect_error", "completion_error"),
    [
        pytest.param("127.0.0.1", errno.ECONNREFUSED, None, id="time-wait"),
        pytest.param(WILDCARD, 0, None, id="accepted"),
        pytest.param("127.0.0.1", errno.EINPROGRESS, None, id="pending"),
        pytest.param("127.0.0.1", errno.EINPROGRESS, errno.ECONNREFUSED, id="delayed-refusal"),
        pytest.param("127.0.0.1", errno.ECONNRESET, None, id="immediate-reset"),
        pytest.param(WILDCARD, errno.EINPROGRESS, errno.ECONNRESET, id="completed-reset"),
    ],
)
def test_macos_port_distinguishes_time_wait_from_a_listener(
    monkeypatch: pytest.MonkeyPatch, requested_host: str, connect_error: int, completion_error: int | None
) -> None:
    clock = Clock()
    port = 8088
    settings = http_settings(monkeypatch, port, requested_host)
    free = errno.ECONNREFUSED in (connect_error, completion_error)
    native_getsockopt = socket.socket.getsockopt

    def connect_ex(sock: socket.socket, address: object) -> int:
        assert sock.gettimeout() == 0.0
        assert address == ("127.0.0.1", port)
        return connect_error

    def bind(sock: socket.socket, address: object) -> None:
        assert address == (requested_host, port)
        if free and not sock.getsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR):
            raise OSError(errno.EADDRINUSE, "TIME_WAIT")

    def listen(sock: socket.socket, backlog: int) -> None:
        pass

    def getsockopt(sock: socket.socket, level: int, option: int) -> int:
        if (level, option) == (socket.SOL_SOCKET, socket.SO_ERROR):
            assert completion_error is not None
            return completion_error
        return native_getsockopt(sock, level, option)

    class ConnectPoll:
        def register(self, sock: socket.socket, events: int) -> None:
            self.fd = sock.fileno()

        def poll(self, timeout: int) -> list[tuple[int, int]]:
            assert 0 <= timeout <= 100
            if completion_error is None or (completion_error == errno.ECONNREFUSED and timeout == 0):
                return []
            return [(self.fd, select.POLLOUT)]

    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(socket.socket, "connect_ex", connect_ex)
    monkeypatch.setattr(socket.socket, "bind", bind)
    monkeypatch.setattr(socket.socket, "listen", listen)
    monkeypatch.setattr(socket.socket, "getsockopt", getsockopt)
    monkeypatch.setattr(select, "poll", ConnectPoll)

    if free:
        sockets = http_serve_sockets(settings, sleep=clock.sleep, clock=clock.clock)
        assert sockets is not None
        for sock in sockets:
            sock.close()
        assert clock.sleeps == []
    else:
        with pytest.raises(SystemExit) as raised:
            sockets = http_serve_sockets(settings, sleep=clock.sleep, clock=clock.clock)
            for sock in sockets or []:
                sock.close()
        assert raised.value.code == f"JEV_MCP_HTTP_PORT={port} is already in use; set JEV_MCP_HTTP_PORT to a free port"


def test_serve_hands_uvicorn_the_prebound_sockets(monkeypatch: pytest.MonkeyPatch) -> None:
    """Uvicorn receives the open listener for HTTP. stdio never calls it."""
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
    settings = http_settings(monkeypatch, port)
    sockets = http_serve_sockets(settings)
    assert sockets is not None
    try:
        anyio.run(serve_call, build_server(settings), settings, sockets)
    finally:
        for sock in sockets:
            sock.close()
    assert handed[-1] is not None
    assert listened == [port]

    monkeypatch.delenv("JEV_MCP_TRANSPORT", raising=False)
    stdio_settings = load_settings()
    assert http_serve_sockets(stdio_settings) is None
    anyio.run(serve_call, build_server(stdio_settings), stdio_settings, None)
    assert len(handed) == 1  # stdio never asks uvicorn to serve


def test_startup_binds_once_before_warmup(monkeypatch: pytest.MonkeyPatch) -> None:
    from jev_judge_mcp import server as server_module
    from jev_judge_mcp.tools import Toolset

    http_settings(monkeypatch, 8088)
    monkeypatch.setattr(sys, "argv", ["jev-judge-mcp"])

    def no_logging(*_args: object) -> None:
        return None

    monkeypatch.setattr(server_module, "configure_process_logging", no_logging)
    monkeypatch.setattr(server_module, "freeze_startup_heap", lambda: None)
    calls: list[str] = []
    issued: list[socket.socket] = []

    def bind(host: str, port: int) -> list[socket.socket]:
        assert (host, port) == ("127.0.0.1", 8088)
        calls.append("bind")
        sock = socket.socket()
        issued.append(sock)
        return [sock]

    async def warm(_: Toolset) -> None:
        calls.append("warm")

    async def serve(_: object, sockets: list[socket.socket] | None = None) -> None:
        assert sockets is not None and sockets == issued[-1:] and sockets[0].fileno() >= 0
        calls.append("serve")

    def exit_process(code: int) -> None:
        raise SystemExit(code)

    monkeypatch.setattr(server_module, "_bind_http_sockets", bind)
    monkeypatch.setattr(Toolset, "awarm", warm)
    monkeypatch.setattr(server_module._UvicornServer, "serve", serve)  # pyright: ignore[reportPrivateUsage]
    monkeypatch.setattr(server_module.os, "_exit", exit_process)
    monkeypatch.setattr(server_module.logging, "shutdown", lambda: None)
    try:
        with pytest.raises(SystemExit) as exited:
            server_module.main()
        assert exited.value.code == 0
        assert calls == ["bind", "warm", "serve"]
        assert all(sock.fileno() == -1 for sock in issued)
    finally:
        for sock in issued:
            sock.close()


def test_uvicorn_shutdown_cancels_a_pending_request(monkeypatch: pytest.MonkeyPatch) -> None:
    import anyio
    from uvicorn.lifespan.off import LifespanOff

    from jev_judge_mcp import server as server_module

    server_class = server_module._UvicornServer  # pyright: ignore[reportPrivateUsage]
    settings = http_settings(monkeypatch, 8088)
    cancelled = False

    async def hold_request() -> None:
        nonlocal cancelled
        try:
            await anyio.sleep_forever()
        finally:
            cancelled = True

    async def shutdown(http: object, sockets: list[socket.socket] | None = None) -> None:
        assert isinstance(http, server_class)
        http.servers = []
        http.lifespan = LifespanOff(http.config)
        pending = asyncio.create_task(hold_request())
        http.server_state.tasks.add(pending)
        try:
            with anyio.fail_after(4):
                await http.shutdown(sockets)
            assert pending.cancelling()
        finally:
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)

    monkeypatch.setattr(server_class, "serve", shutdown)
    run_transport = server_module._serve  # pyright: ignore[reportPrivateUsage]
    with socket.socket() as listener:
        anyio.run(run_transport, server_module.build_server(settings), settings, [listener])
    assert cancelled
