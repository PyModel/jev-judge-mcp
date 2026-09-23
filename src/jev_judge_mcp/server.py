"""MCP entry point: stdio by default, Streamable HTTP when `JEV_MCP_TRANSPORT=streamable-http`.

stdout belongs to the protocol. Logging goes to stderr, and SIGINT/SIGTERM stop the
server and exit 0 without a traceback. `install`, `hook`, and `doctor` are dispatched
before the server starts.
"""

import contextlib
import gc
import logging
import os
import signal
import sys
from collections.abc import Callable, Generator, Iterable
from importlib.metadata import version
from typing import Any, override

import anyio
import uvicorn
from mcp.server.mcpserver import Context, MCPServer
from mcp.shared.exceptions import MCPError
from mcp.types import INTERNAL_ERROR, CallToolResult, CancelledNotificationParams, NotificationParams, Tool

from jev_judge_mcp.errors import RedactingFilter, Redactor
from jev_judge_mcp.serialize import stringify
from jev_judge_mcp.settings import LogLevel, Settings, load_settings
from jev_judge_mcp.stdio import stdio_streams
from jev_judge_mcp.tools import TOOLS, Runtime, Toolset

SERVER_NAME = "jev-mcp"
DISTRIBUTION = "jev-judge-mcp"

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


class JevMCPServer(MCPServer):
    """`MCPServer` whose `tools/list` and `tools/call` both go through one `Toolset` (ADR-0013).

    The decorator path derives schemas from Python signatures: it adds `title` keys, an
    `outputSchema`, and never emits `execution`, so it cannot reproduce the TS snapshot
    (ADR-0006, ADR-0010). The Toolset is the single registry: what it publishes is what it
    dispatches, and it returns the reference's result shapes itself.
    """

    def __init__(self, *, toolset: Toolset, log_level: LogLevel) -> None:
        super().__init__(name=SERVER_NAME, version=version(DISTRIBUTION), log_level=log_level)
        self.toolset = toolset
        # The SDK acts on both before any handler runs (the dispatcher cancels the request, the
        # runner marks the session initialized); without a handler it logs each as unhandled.
        self._lowlevel_server.add_notification_handler(
            "notifications/cancelled", CancelledNotificationParams, _already_handled
        )
        self._lowlevel_server.add_notification_handler(
            "notifications/initialized", NotificationParams, _already_handled
        )

    @override
    async def list_tools(self) -> list[Tool]:
        return self.toolset.definitions()

    @override
    async def call_tool(
        self, name: str, arguments: dict[str, Any], context: Context[Any, Any] | None = None
    ) -> CallToolResult:
        # The SDK hands a missing or null `arguments` over as `{}`. The raw params tell them apart:
        # the reference validates a missing one as `undefined`, which reports the root instead of each
        # required field, and its request schema rejects null before any tool.
        params = {} if context is None else context.request_context.params or {}
        if "arguments" in params and params["arguments"] is None:
            raise MCPError(code=INTERNAL_ERROR, message=NULL_ARGUMENTS)
        sent = context is None or "arguments" in params
        return await self.toolset.call(name, arguments if sent else None)

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


def configure_logging(level: LogLevel, secrets: Iterable[str] = ()) -> None:
    """Log to stderr. Every record, from any logger, is redacted of `secrets` first (ADR-0008)."""
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    handler.addFilter(RedactingFilter(Redactor(secrets)))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)


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


async def serve(server: JevMCPServer, settings: Settings) -> None:
    try:
        await _serve(server, settings)
    finally:
        with anyio.CancelScope(shield=True):
            await server.toolset.aclose()


async def _serve(server: JevMCPServer, settings: Settings) -> None:
    async with anyio.create_task_group() as tg:
        if settings.transport == "stdio":
            tg.start_soon(_stop_on_signal, tg.cancel_scope.cancel)
            await server.run_stdio_async()
        else:
            app = server.streamable_http_app(host=settings.http_host)
            # log_config=None: uvicorn's loggers propagate to the stderr root handler.
            config = uvicorn.Config(app, host=settings.http_host, port=settings.http_port, log_config=None)
            http = _UvicornServer(config)

            def stop() -> None:
                http.should_exit = True

            tg.start_soon(_stop_on_signal, stop)
            await http.serve()
        tg.cancel_scope.cancel()


def freeze_startup_heap() -> None:
    """Move everything allocated so far (modules, schemas, the toolset) out of the collector's reach.

    None of it becomes garbage, and a full collection that rescans it pauses every in-flight call
    for 10-15 ms, which alone breaks the P9 p95 budget at 64 concurrent calls (`make load`).
    """
    gc.collect()
    gc.freeze()


def require_posix() -> None:
    """Refuse a non-POSIX platform before settings, signals, or the extract worker pool (ADR-0032).

    CPython reports Windows as `sys.platform == "win32"`. Any `win*` name is the same family.
    The message names that platform. `SystemExit` with a string prints once and exits 1.
    """
    if sys.platform.startswith("win"):
        raise SystemExit(
            f"unsupported platform {sys.platform}: jev-judge-mcp is POSIX-only; "
            "Windows is unsupported until Windows-specific behavior is implemented and tested"
        )


def installer_requested(argv: list[str]) -> bool:
    """True only for the `install` subcommand. No arguments, and every other argument, stay the server."""
    return len(argv) > 1 and argv[1] == "install"


def hook_requested(argv: list[str]) -> bool:
    """True only for the `hook` subcommand. No arguments, and every other argument, stay the server."""
    return len(argv) > 1 and argv[1] == "hook"


def doctor_requested(argv: list[str]) -> bool:
    """True only for the `doctor` subcommand. No arguments, and every other argument, stay the server."""
    return len(argv) > 1 and argv[1] == "doctor"


def setup_requested(argv: list[str]) -> bool:
    """True only for the `setup` subcommand. No arguments, and every other argument, stay the server."""
    return len(argv) > 1 and argv[1] == "setup"


def main() -> None:
    # The platform gate covers the installer, the command hook, the doctor, setup, and the server.
    # No arguments keep stdout protocol-only (ADR-0032, ADR-0033).
    require_posix()
    if installer_requested(sys.argv):
        from jev_judge_mcp.install.cli import main as install_main

        sys.exit(install_main(sys.argv[2:]))
    if hook_requested(sys.argv):
        from jev_judge_mcp.hook import main as hook_main

        sys.exit(hook_main(sys.argv[2:]))
    if doctor_requested(sys.argv):
        from jev_judge_mcp.doctor import main as doctor_main

        sys.exit(doctor_main(sys.argv[2:]))
    if setup_requested(sys.argv):
        from jev_judge_mcp.setup import main as setup_main

        sys.exit(setup_main(sys.argv[2:]))
    settings = load_settings()
    configure_logging(settings.log_level, settings.secret_values())
    server = build_server(settings)
    freeze_startup_heap()
    anyio.run(serve, server, settings)
    # A stdin read abandoned at shutdown still blocks one of anyio's non-daemon worker threads, and
    # interpreter exit would join it until the client closes stdin. `serve` has already closed
    # everything the server owns: the wire wrapper flushed every frame it wrote, and flushing fd 1
    # again here could only push stray buffered bytes onto the wire. Leave without joining it.
    logging.shutdown()
    sys.stderr.flush()
    os._exit(0)
