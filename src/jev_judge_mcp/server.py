"""MCP entry point: stdio by default, Streamable HTTP when `JEV_MCP_TRANSPORT=streamable-http`.

stdout belongs to the protocol. Logging goes to stderr, and SIGINT/SIGTERM stop the
server and exit 0 without a traceback. `install`, `hook`, and `doctor` are dispatched
before the server starts.
"""

import contextlib
import errno
import gc
import logging
import os
import signal
import socket
import sys
import time
from collections.abc import Callable, Generator, Iterable
from importlib.metadata import PackageNotFoundError
from typing import Any, override

import anyio
import uvicorn
from mcp.server.mcpserver import Context, MCPServer
from mcp.shared.exceptions import MCPError
from mcp.types import INTERNAL_ERROR, CallToolResult, CancelledNotificationParams, NotificationParams, Tool
from starlette.types import ASGIApp

from jev_judge_mcp import keyfile
from jev_judge_mcp.errors import RedactingFilter, Redactor
from jev_judge_mcp.http_auth import BearerTokenMiddleware, ensure_http_access_control
from jev_judge_mcp.identity import reported_version
from jev_judge_mcp.instructions import server_instructions
from jev_judge_mcp.serialize import stringify
from jev_judge_mcp.settings import LogLevel, Settings, load_settings
from jev_judge_mcp.stdio import stdio_streams
from jev_judge_mcp.tools import TOOLS, Runtime, Toolset

SERVER_NAME = "jev-mcp"
DISTRIBUTION = "jev-judge-mcp"

MIN_SECRET_LENGTH = 8
"""Shortest configured secret the Redactor accepts: shorter values would blank matching text."""

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
        super().__init__(
            name=SERVER_NAME,
            version=reported_version(),
            instructions=server_instructions(toolset.names()),
            log_level=log_level,
        )
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


HTTP_BIND_ATTEMPTS = 4
"""Tries of a taken HTTP port before exit. A few, not a hunt for a free one (ADR-0055)."""

HTTP_BIND_BUDGET_S = 2.0
"""Seconds those tries may take, waits included. The port never changes."""


def http_port_in_use_message(port: int) -> str:
    """The one line a taken Streamable HTTP port exits with (ADR-0055)."""
    return f"JEV_MCP_HTTP_PORT={port} is already in use; set JEV_MCP_HTTP_PORT to a free port"


def ensure_http_port_free(
    settings: Settings,
    *,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> None:
    """Refuse a Streamable HTTP start whose port stays taken (ADR-0055).

    The configured port is tried `HTTP_BIND_ATTEMPTS` times, and the waits stay inside
    `HTTP_BIND_BUDGET_S`. It is never replaced with another port. Any other bind error fails on
    the first try. Runs before logging and before the server listens, so a refusal is one line on
    stderr, exit 1, and no listener or worker. stdio never binds. A host that does not resolve is
    left to the server. `sleep` and `clock` are the production clock unless a test injects them.
    """
    if settings.transport != "streamable-http":
        return
    host, port = settings.http_host, settings.http_port
    deadline = clock() + HTTP_BIND_BUDGET_S
    gap = HTTP_BIND_BUDGET_S / (HTTP_BIND_ATTEMPTS - 1)
    for attempt in range(1, HTTP_BIND_ATTEMPTS + 1):
        try:
            _bind_http(host, port)
        except OSError as exc:
            if exc.errno != errno.EADDRINUSE:
                raise
            remaining = deadline - clock()
            if attempt == HTTP_BIND_ATTEMPTS or remaining <= 0:
                raise SystemExit(http_port_in_use_message(port)) from None
            sleep(min(gap, remaining))
        else:
            return
    raise SystemExit(http_port_in_use_message(port))


def _bind_http_sockets(host: str, port: int) -> list[socket.socket]:
    """Bind every address `host` resolves to and keep the sockets open. A taken port raises
    `EADDRINUSE`; a host that does not resolve returns no sockets.

    `SO_REUSEADDR` matches the socket uvicorn binds on POSIX. Without it, a restart while the
    previous process's connections sit in `TIME_WAIT` looks taken, and the process exits even
    though the real bind would succeed. A socket that is still listening is still `EADDRINUSE`.
    `IPV6_V6ONLY` stays set so an IPv6 probe does not also claim the IPv4 port.
    """
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror:
        return []
    sockets: list[socket.socket] = []
    for family, socktype, proto, _canon, sockaddr in infos:
        sock = socket.socket(family, socktype, proto)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            if family == socket.AF_INET6:
                sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
            sock.bind(sockaddr)
        except BaseException:
            sock.close()
            for opened in sockets:
                opened.close()
            raise
        sockets.append(sock)
    return sockets


def _bind_http(host: str, port: int) -> None:
    """The probe form of `_bind_http_sockets`: bind every address, then close. A taken port
    raises `EADDRINUSE`."""
    for sock in _bind_http_sockets(host, port):
        sock.close()


def ensure_secrets_redactable(settings: Settings) -> None:
    """Refuse any configured secret too short to redact safely (ADR-0050).

    The Redactor replaces exact values, so a 1-2 character secret would blank every matching
    fragment of tool output and logs. Each configured `SecretStr` setting and the key-file key
    (ADR-0046) is held to `MIN_SECRET_LENGTH`; the error names the variable, never the value.
    """
    for variable, value in settings.named_secrets():
        if len(value) < MIN_SECRET_LENGTH:
            raise SystemExit(
                f"{variable} is shorter than {MIN_SECRET_LENGTH} characters; refusing to start: "
                "a secret that short cannot be redacted without corrupting text"
            )
    stored = keyfile.stored_key(settings)
    if stored and len(stored) < MIN_SECRET_LENGTH:
        raise SystemExit(
            f"the key file {keyfile.stored_key_path(settings)} set by JEV_MCP_KEY_FILE holds a key shorter "
            f"than {MIN_SECRET_LENGTH} characters; refusing to start: a secret that short cannot be "
            "redacted without corrupting text"
        )


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
        # The served pool starts warm (ADR-0058): filled before any transport runs, so a burst
        # arrival never pays worker startup inside a call's deadline.
        await server.toolset.awarm()
        await _serve(server, settings)
    finally:
        with anyio.CancelScope(shield=True):
            await server.toolset.aclose()


async def _serve(server: JevMCPServer, settings: Settings) -> None:
    sockets = http_serve_sockets(settings)
    async with anyio.create_task_group() as tg:
        if settings.transport == "stdio":
            tg.start_soon(_stop_on_signal, tg.cancel_scope.cancel)
            await server.run_stdio_async()
        else:
            app = http_asgi_app(server, settings)
            # log_config=None: uvicorn's loggers propagate to the stderr root handler.
            config = uvicorn.Config(app, host=settings.http_host, port=settings.http_port, log_config=None)
            http = _UvicornServer(config)

            def stop() -> None:
                http.should_exit = True

            tg.start_soon(_stop_on_signal, stop)
            # The sockets this process bound are the ones uvicorn listens on: nothing can take
            # the port between the startup gate and the listen (ADR-0055 amendment).
            await http.serve(sockets=sockets)
        tg.cancel_scope.cancel()


def http_serve_sockets(settings: Settings) -> list[socket.socket] | None:
    """The listening sockets `serve` hands to uvicorn, or `None` when uvicorn binds its own.

    The startup gate (`ensure_http_port_free`) probes and closes; this bind is the listen. A
    port taken in that gap refuses here with ADR-0055's one line, not with uvicorn's own bind
    error. Any other bind error fails as itself, and a host that does not resolve is left to
    the server, exactly as in the gate.
    """
    if settings.transport != "streamable-http":
        return None
    try:
        return _bind_http_sockets(settings.http_host, settings.http_port) or None
    except OSError as exc:
        if exc.errno == errno.EADDRINUSE:
            raise SystemExit(http_port_in_use_message(settings.http_port)) from None
        raise


def http_asgi_app(server: JevMCPServer, settings: Settings) -> ASGIApp:
    """The Streamable HTTP app, behind the bearer-token gate when a token is set (ADR-0050).

    The Host/Origin (DNS-rebinding) validation is the SDK's own: automatic exactly on the
    protected hosts (`127.0.0.1`, `localhost`, `::1`), untouched here. Every other host must have
    passed the startup gate, and the token is its access control.
    """
    app: ASGIApp = server.streamable_http_app(host=settings.http_host)
    if settings.http_token is not None:
        app = BearerTokenMiddleware(app, settings.http_token)
    return app


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


def judge_requested(argv: list[str]) -> bool:
    return len(argv) > 1 and argv[1] == "judge"


def gate_cli_requested(argv: list[str]) -> bool:
    return len(argv) > 1 and argv[1] == "gate"


def completion_hook_requested(argv: list[str]) -> bool:
    return len(argv) > 1 and argv[1] == "completion-hook"


def version_requested(argv: list[str]) -> bool:
    """True only for `--version`. It prints the build identity and does not start the server (ADR-0054)."""
    return len(argv) > 1 and argv[1] == "--version"


def help_requested(argv: list[str]) -> bool:
    """True only for `--help`/`-h`. It prints usage and does not start the server."""
    return len(argv) > 1 and argv[1] in ("--help", "-h")


USAGE = """\
usage: jev-judge-mcp [<subcommand>]

With no arguments it serves MCP over stdio. JEV_MCP_TRANSPORT=streamable-http serves
Streamable HTTP instead (JEV_MCP_HTTP_PORT, JEV_MCP_HTTP_TOKEN).

subcommands:
  install            install the server into a harness config
  hook               run the completion gate hook
  doctor             diagnose the local setup
  setup              store the TypeSafe API key
  judge              call one tool; one JSON object on stdin
  gate               review a git range from local repo files
  completion-hook    opt-in completion gate
  --version          print the build identity
  --help, -h         print this usage
"""


def print_usage() -> None:
    """Every subcommand, on stdout, then exit 0 (the same surface as `--version`)."""
    print(USAGE, end="")


def print_version() -> None:
    """The same identity `initialize` reports, on stdout, then exit 0."""
    try:
        identity = reported_version()
    except PackageNotFoundError:
        raise SystemExit(f"{DISTRIBUTION} is not installed; its version cannot be reported") from None
    print(f"{DISTRIBUTION} {identity}")


def main() -> None:
    # The platform gate covers the installer, the command hook, the doctor, setup, and the server.
    # No arguments keep stdout protocol-only (ADR-0032, ADR-0033).
    require_posix()
    if version_requested(sys.argv):
        print_version()
        return
    if help_requested(sys.argv):
        print_usage()
        return
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
    if judge_requested(sys.argv) or gate_cli_requested(sys.argv) or completion_hook_requested(sys.argv):
        ensure_secrets_redactable(load_settings())
    if judge_requested(sys.argv):
        from jev_judge_mcp.cli import judge_main

        sys.exit(judge_main(sys.argv[2:]))
    if gate_cli_requested(sys.argv):
        from jev_judge_mcp.cli import gate_main

        sys.exit(gate_main(sys.argv[2:]))
    if completion_hook_requested(sys.argv):
        from jev_judge_mcp.cli import completion_hook_main

        sys.exit(completion_hook_main(sys.argv[2:]))
    settings = load_settings()
    # The gates run before logging and before anything binds, so a misconfiguration is one clear
    # line on stderr and a non-zero exit, never a live unauthenticated server (ADR-0050).
    ensure_secrets_redactable(settings)
    ensure_http_access_control(settings)
    ensure_http_port_free(settings)
    configure_logging(settings.log_level, settings.secret_values())
    server = build_server(settings)
    # One line, the same identity `initialize` will report (ADR-0054). Not logged by `--version`.
    logger.info("identity %s %s", server.name, server.version)
    freeze_startup_heap()
    anyio.run(serve, server, settings)
    # A stdin read abandoned at shutdown still blocks one of anyio's non-daemon worker threads, and
    # interpreter exit would join it until the client closes stdin. `serve` has already closed
    # everything the server owns: the wire wrapper flushed every frame it wrote, and flushing fd 1
    # again here could only push stray buffered bytes onto the wire. Leave without joining it.
    logging.shutdown()
    sys.stderr.flush()
    os._exit(0)
