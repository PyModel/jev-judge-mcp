"""MCP entry point: stdio by default, Streamable HTTP when `JEV_MCP_TRANSPORT=streamable-http`.

stdout belongs to the protocol. Logging goes to stderr. SIGINT and SIGTERM stop the server
with exit status 0. Uvicorn can log requests cancelled at its shutdown deadline.
"""

import contextlib
import errno
import gc
import logging
import os
import select
import signal
import socket
import sys
import time
from collections.abc import AsyncGenerator, Callable, Generator
from typing import Any, override

import anyio
import uvicorn
from anyio.abc import TaskGroup
from mcp.server.mcpserver import Context, MCPServer
from mcp.shared.exceptions import MCPError
from mcp.types import INTERNAL_ERROR, CallToolResult, CancelledNotificationParams, NotificationParams, TextContent, Tool
from starlette.types import ASGIApp

from jev_judge_mcp.http_auth import BearerTokenMiddleware, ensure_http_access_control, require_http_token
from jev_judge_mcp.identity import ServerVersion, reported_version
from jev_judge_mcp.instructions import server_instructions
from jev_judge_mcp.redaction.values import Redactor, configure_process_logging
from jev_judge_mcp.security import build_guard
from jev_judge_mcp.serialize import stringify
from jev_judge_mcp.settings import LogLevel, Settings, load_settings
from jev_judge_mcp.skills import attach as attach_skills
from jev_judge_mcp.stdio import stdio_streams
from jev_judge_mcp.tools import TOOLS, Runtime, Toolset

SERVER_NAME = "jev-mcp"

logger = logging.getLogger("jev_judge_mcp")

NULL_ARGUMENTS = stringify(
    [
        {
            "expected": "record",
            "code": "invalid_type",
            "path": ["params", "arguments"],
            "message": "Invalid input: expected record, received null",
        }
    ]
)
"""The TS SDK's text for `"arguments": null`: its request schema's zod issues, as a -32603."""


class JevMCPServer(MCPServer[TaskGroup]):
    """`MCPServer` whose `tools/list` and `tools/call` both go through one `Toolset` (ADR-0013).

    The decorator path derives schemas from Python signatures: it adds `title` keys, an
    `outputSchema`, and never emits `execution`, so it cannot reproduce the TS snapshot
    (ADR-0006, ADR-0010). The Toolset is the single registry: what it publishes is what it
    dispatches, and it returns the reference's result shapes itself.
    """

    def __init__(self, *, toolset: Toolset, log_level: LogLevel) -> None:
        super().__init__(
            name=SERVER_NAME,
            version=reported_version(),
            instructions=server_instructions(toolset.names()),
            log_level=log_level,
            lifespan=self._version_lifespan,
        )
        self.code_version = ServerVersion()
        self.toolset = toolset
        attach_skills(self)
        # The SDK acts on both before any handler runs (the dispatcher cancels the request, the
        # runner marks the session initialized); without a handler it logs each as unhandled.
        self._lowlevel_server.add_notification_handler(
            "notifications/cancelled", CancelledNotificationParams, _already_handled
        )
        self._lowlevel_server.add_notification_handler(
            "notifications/initialized", NotificationParams, _already_handled
        )

    @contextlib.asynccontextmanager
    async def _version_lifespan(self, _: MCPServer[TaskGroup]) -> AsyncGenerator[TaskGroup]:
        async with anyio.create_task_group() as tasks:
            try:
                yield tasks
            finally:
                tasks.cancel_scope.cancel()

    @override
    async def list_tools(self) -> list[Tool]:
        return self.toolset.definitions()

    @override
    async def call_tool(
        self, name: str, arguments: dict[str, Any], context: Context[TaskGroup, Any] | None = None
    ) -> CallToolResult:
        # The SDK hands a missing or null `arguments` over as `{}`. The raw params tell them apart:
        # the reference validates a missing one as `undefined`, which reports the root instead of each
        # required field, and its request schema rejects null before any tool.
        params = {} if context is None else context.request_context.params or {}
        if "arguments" in params and params["arguments"] is None:
            raise MCPError(code=INTERNAL_ERROR, message=NULL_ARGUMENTS)
        if context is not None:
            self.code_version.refresh(context.request_context.lifespan_context)
        sent = context is None or "arguments" in params
        result = await self.toolset.call(name, arguments if sent else None)
        version = self.code_version.field()
        result.meta = {**(result.meta or {}), "jev/version": version}
        if notice := version.get("notice"):
            result.content.append(TextContent(type="text", text=str(notice)))
        return result

    async def run_stdio_async(self) -> None:
        """The SDK's stdio loop over this server's own transport (`jev_judge_mcp.stdio`)."""
        async with stdio_streams() as (read_stream, write_stream):
            lowlevel = self._lowlevel_server
            await lowlevel.run(read_stream, write_stream, lowlevel.create_initialization_options())


async def _already_handled(ctx: object, params: object) -> None:
    return None


def build_server(settings: Settings) -> JevMCPServer:
    """The server with every tool; `tools/list` and `tools/call` share the Toolset's one registry."""
    return JevMCPServer(toolset=Toolset(Runtime(settings), TOOLS), log_level=settings.log_level)


HTTP_BIND_ATTEMPTS = 4
"""Tries of a taken HTTP port before exit. A few, not a hunt for a free one (ADR-0055)."""

HTTP_BIND_BUDGET_S = 2.0
"""Seconds those tries may take, waits included. The port never changes."""

HTTP_CONNECT_PROBE_MS = 50
"""Maximum time for an asynchronous local TCP probe to report its result."""


HTTP_GRACEFUL_SHUTDOWN_SECONDS = 2
"""Time in seconds for in-flight HTTP requests to drain before uvicorn cancels them."""


def http_port_in_use_message(port: int) -> str:
    """The one line a taken Streamable HTTP port exits with (ADR-0055)."""
    return f"JEV_MCP_HTTP_PORT={port} is already in use; set JEV_MCP_HTTP_PORT to a free port"


def http_serve_sockets(
    settings: Settings,
    *,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> list[socket.socket] | None:
    """Hold the configured HTTP port before warming workers (ADR-0055). stdio never binds."""
    if settings.transport != "streamable-http":
        return None
    host, port = settings.http_host, settings.http_port
    deadline = clock() + HTTP_BIND_BUDGET_S
    gap = HTTP_BIND_BUDGET_S / (HTTP_BIND_ATTEMPTS - 1)
    for attempt in range(1, HTTP_BIND_ATTEMPTS + 1):
        try:
            return _bind_http_sockets(host, port)
        except OSError as exc:
            if exc.errno != errno.EADDRINUSE:
                raise
            remaining = deadline - clock()
            if attempt == HTTP_BIND_ATTEMPTS or remaining <= 0:
                raise SystemExit(http_port_in_use_message(port)) from None
            sleep(min(gap, remaining))
    raise SystemExit(http_port_in_use_message(port))


def _bind_http_sockets(host: str, port: int) -> list[socket.socket]:
    """Listen on every address `host` resolves to, with cleanup on a partial bind."""
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror:
        raise SystemExit("JEV_MCP_HTTP_HOST does not resolve to a listen address") from None
    sockets: list[socket.socket] = []
    with contextlib.ExitStack() as opened:
        for family, socktype, proto, _canon, sockaddr in infos:
            host_ip = sockaddr[0]
            # Reuse permits TIME_WAIT restarts. Where reuse allows wildcard overlap,
            # a connected or still-pending probe must refuse the bind.
            if not sys.platform.startswith("linux") and isinstance(host_ip, str):
                loopback = {"0.0.0.0": "127.0.0.1", "::": "::1"}.get(host_ip)  # noqa: S104
                address = (loopback, *sockaddr[1:]) if loopback else sockaddr
                pending = (errno.EINPROGRESS, errno.EALREADY, errno.EWOULDBLOCK, errno.EINTR)
                with socket.socket(family, socktype, proto) as probe:
                    probe.setblocking(False)
                    error = probe.connect_ex(address)
                    if error in pending:
                        poller = select.poll()
                        poller.register(probe, select.POLLOUT)
                        if poller.poll(HTTP_CONNECT_PROBE_MS):
                            error = probe.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR)
                if error in (0, errno.EISCONN, errno.ECONNRESET, *pending):
                    raise OSError(errno.EADDRINUSE, os.strerror(errno.EADDRINUSE))
                if error != errno.ECONNREFUSED:
                    raise OSError(error, os.strerror(error))
            sock = opened.enter_context(socket.socket(family, socktype, proto))
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            if family == socket.AF_INET6:
                sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
            sock.bind(sockaddr)
            sock.listen(socket.SOMAXCONN)
            sockets.append(sock)
        opened.pop_all()
    return sockets


def ensure_secrets_redactable(settings: Settings) -> Redactor:
    try:
        return build_guard(settings).redact
    except ValueError as error:
        raise SystemExit(str(error)) from None


class _UvicornServer(uvicorn.Server):
    """Leaves signals to `serve`, which owns them for both transports."""

    @contextlib.contextmanager
    def capture_signals(self) -> Generator[None]:
        yield


async def _stop_on_signal(stop: Callable[[], None]) -> None:
    with anyio.open_signal_receiver(signal.SIGINT, signal.SIGTERM) as signals:
        async for signum in signals:
            logger.info("received %s, shutting down", signal.Signals(signum).name)
            stop()
            return


async def serve(server: JevMCPServer, settings: Settings, sockets: list[socket.socket] | None = None) -> None:
    try:
        # The served pool starts warm (ADR-0058): filled before any transport runs, so a burst
        # arrival never pays worker startup inside a call's deadline.
        await server.toolset.awarm()
        await _serve(server, settings, sockets)
    finally:
        with anyio.CancelScope(shield=True):
            await server.toolset.aclose()


async def _serve(server: JevMCPServer, settings: Settings, sockets: list[socket.socket] | None) -> None:
    async with anyio.create_task_group() as tg:
        if settings.transport == "stdio":
            tg.start_soon(_stop_on_signal, tg.cancel_scope.cancel)
            await server.run_stdio_async()
        else:
            assert sockets is not None
            app = http_asgi_app(server, settings)
            # log_config=None: uvicorn's loggers propagate to the stderr root handler.
            config = uvicorn.Config(
                app,
                host=settings.http_host,
                port=settings.http_port,
                log_config=None,
                timeout_graceful_shutdown=HTTP_GRACEFUL_SHUTDOWN_SECONDS,
            )
            http = _UvicornServer(config)

            def stop() -> None:
                http.should_exit = True

            tg.start_soon(_stop_on_signal, stop)
            await http.serve(sockets=sockets)
        tg.cancel_scope.cancel()


def http_asgi_app(server: JevMCPServer, settings: Settings) -> ASGIApp:
    """Require a bearer token in addition to the SDK's automatic Host/Origin checks."""
    token = require_http_token(settings)
    return BearerTokenMiddleware(server.streamable_http_app(host=settings.http_host), token)


def freeze_startup_heap() -> None:
    """Move everything allocated so far (modules, schemas, the toolset) out of the collector's reach.

    None of it becomes garbage, and a full collection that rescans it pauses every in-flight call
    for 10-15 ms, which alone breaks the P9 p95 budget at 64 concurrent calls (`make load`).
    """
    gc.collect()
    gc.freeze()


def main() -> None:
    settings = load_settings()
    # The gates run before logging and before anything binds, so a misconfiguration is one clear
    # line on stderr and a non-zero exit, never a live unauthenticated server (ADR-0050).
    ensure_http_access_control(settings)
    redact = ensure_secrets_redactable(settings)
    with contextlib.ExitStack() as listeners:
        sockets = http_serve_sockets(settings)
        for sock in sockets or ():
            listeners.enter_context(sock)
        configure_process_logging(settings, redact)
        server = build_server(settings)
        # One line, the same identity `initialize` will report (ADR-0054).
        logger.info("identity %s %s", server.name, server.version)
        freeze_startup_heap()
        anyio.run(serve, server, settings, sockets)
    # A stdin read abandoned at shutdown still blocks one of anyio's non-daemon worker threads, and
    # interpreter exit would join it until the client closes stdin. `serve` has already closed
    # everything the server owns: the wire wrapper flushed every frame it wrote, and flushing fd 1
    # again here could only push stray buffered bytes onto the wire. Leave without joining it.
    logging.shutdown()
    sys.stderr.flush()
    os._exit(0)
