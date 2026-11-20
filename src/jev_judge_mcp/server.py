"""MCP entry point: stdio by default, Streamable HTTP when `JEV_MCP_TRANSPORT=streamable-http`.

stdout belongs to the protocol. Logging goes to stderr. SIGINT and SIGTERM stop the server
with exit status 0. Uvicorn can log requests cancelled at its shutdown deadline. `install`,
`hook`, and `doctor` are dispatched before the server starts.
"""

import contextlib
import errno
import gc
import io
import logging
import os
import select
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
from mcp.types import INTERNAL_ERROR, CallToolResult, CancelledNotificationParams, NotificationParams, TextContent, Tool
from pydantic import ValidationError
from starlette.types import ASGIApp

from jev_judge_mcp import keyfile
from jev_judge_mcp.errors import RedactingFilter, Redactor
from jev_judge_mcp.http_auth import BearerTokenMiddleware, ensure_http_access_control, require_http_token
from jev_judge_mcp.identity import ServerVersion, reported_version
from jev_judge_mcp.instructions import server_instructions
from jev_judge_mcp.serialize import stringify
from jev_judge_mcp.settings import LogLevel, Settings, load_settings, unexpanded_references
from jev_judge_mcp.skills import attach as attach_skills
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
        result = await self.toolset.call(name, arguments if sent else None)
        version = await self.code_version.field()
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


def configure_logging(level: LogLevel, secrets: Iterable[str] = ()) -> None:
    """Log to stderr. Every record, from any logger, is redacted of `secrets` first (ADR-0008)."""
    handler = logging.StreamHandler(sys.stderr)
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    handler.addFilter(RedactingFilter(Redactor(secrets)))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)


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


def warn_unexpanded_references() -> None:
    """Name each credential the harness passed through unexpanded; it was read as unset (ADR-0046)."""
    for variable in unexpanded_references():
        logger.warning("%s holds an unexpanded reference and counts as unset", variable)


def ensure_secrets_redactable(settings: Settings) -> None:
    try:
        validate_secrets_redactable(settings)
    except ValueError as error:
        raise SystemExit(str(error)) from None


def validate_secrets_redactable(settings: Settings) -> None:
    """Refuse any configured secret too short to redact safely (ADR-0050).

    The Redactor replaces exact values, so a 1-2 character secret would blank every matching
    fragment of tool output and logs. Each configured `SecretStr` setting and the key-file key
    (ADR-0046) is held to `MIN_SECRET_LENGTH`; the error names the variable, never the value.
    """
    for variable, value in settings.named_secrets():
        if variable == "TYPESAFE_API_KEY":
            value = value.strip() or value
        if len(value) < MIN_SECRET_LENGTH:
            raise ValueError(
                f"{variable} is shorter than {MIN_SECRET_LENGTH} characters; refusing to start: "
                "a secret that short cannot be redacted without corrupting text"
            )
    stored = keyfile.stored_key(settings)
    if stored and len(stored) < MIN_SECRET_LENGTH:
        where = f"the key file {keyfile.stored_key_path(settings)}"
        if settings.key_file is not None:
            where += " set by JEV_MCP_KEY_FILE"
        raise ValueError(
            f"{where} holds a key shorter than {MIN_SECRET_LENGTH} characters; refusing to start: "
            "a secret that short cannot be redacted without corrupting text"
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


def calibrate_requested(argv: list[str]) -> bool:
    """True only for the `calibrate` subcommand (ADR-0070). Offline; no provider, no secrets."""
    return len(argv) > 1 and argv[1] == "calibrate"


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
  hook               run gate, screen, or compact-cut
  doctor             diagnose the local setup
  setup              store the TypeSafe API key
  calibrate          advisory threshold fitting on your labeled rows (offline)
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


def _configure_process_logging(settings: Settings) -> None:
    configure_logging(settings.log_level, keyfile.redaction_values(settings))
    warn_unexpanded_references()


def _run_hook(argv: list[str]) -> int:
    from jev_judge_mcp.hook import fail_open_or_ask, hook_required

    annotates = argv[1:3] in (["hook", "screen"], ["hook", "compact-cut"])
    required = not annotates and hook_required(os.environ)
    try:
        stdin = sys.stdin
        if isinstance(stdin, io.TextIOWrapper):
            stdin.reconfigure(encoding="utf-8", errors="strict")
        if hook_requested(argv):
            from jev_judge_mcp.hook import main as hook_main

            return hook_main(argv[2:])
        from jev_judge_mcp.cli import completion_hook_main

        settings = load_settings()
        validate_secrets_redactable(settings)
        _configure_process_logging(settings)
        return completion_hook_main(argv[2:])
    except ValidationError as error:
        sys.stderr.write(_settings_error(error) + "\n")
        return fail_open_or_ask(required, "", "invalid settings")
    except Exception:
        return fail_open_or_ask(required, "jev-judge-mcp hook: unexpected error\n", "hook failed")


def _settings_error(error: ValidationError) -> str:
    problems: list[str] = []
    for issue in error.errors(include_input=False, include_context=True, include_url=False):
        variable = issue["loc"][0]
        kind = issue["type"]
        if kind.startswith("bool"):
            accepted = "true/false, 1/0, yes/no, on/off, y/n or t/f"
        elif variable == "JEV_MCP_HTTP_PORT":
            accepted = "an integer from 1 through 65535"
        elif kind.startswith("int") or variable in ("JEV_MCP_MAX_INFLIGHT", "JEV_MCP_CACHE_MAX_ENTRIES"):
            accepted = "a non-negative integer"
        elif variable == "JEV_MCP_CACHE_TTL_SECONDS":
            accepted = "a non-negative number of seconds"
        elif kind == "literal_error":
            accepted = str(issue.get("ctx", {}).get("expected", "a supported value"))
        elif variable in ("JEV_MCP_KEY_FILE", "JEV_MCP_CACHE_DIR", "XDG_CACHE_HOME", "XDG_CONFIG_HOME"):
            accepted = "a filesystem path with a resolvable home directory"
        else:
            accepted = "text"
        problems.append(f"{variable} must be {accepted}")
    return "Invalid settings: " + ", ".join(problems)


def main() -> None:
    try:
        _main()
    except ValidationError as error:
        raise SystemExit(_settings_error(error)) from None


def _main() -> None:
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
    if hook_requested(sys.argv) or completion_hook_requested(sys.argv):
        sys.exit(_run_hook(sys.argv))
    if doctor_requested(sys.argv):
        from jev_judge_mcp.doctor import main as doctor_main

        sys.exit(doctor_main(sys.argv[2:]))
    if setup_requested(sys.argv):
        from jev_judge_mcp.setup import main as setup_main

        sys.exit(setup_main(sys.argv[2:]))
    if calibrate_requested(sys.argv):
        from jev_judge_mcp.cli import calibrate_main

        sys.exit(calibrate_main(sys.argv[2:]))
    if judge_requested(sys.argv) or gate_cli_requested(sys.argv):
        from jev_judge_mcp.cli import emit_configuration_failure, gate_main, judge_main

        judge = judge_requested(sys.argv)
        args = sys.argv[2:]
        tool = (args[0] if args else "") if judge else "jev_gate"
        try:
            subcommand_settings = load_settings()
        except ValidationError as error:
            sys.exit(emit_configuration_failure(tool, "invalid_arguments", _settings_error(error)))
        try:
            validate_secrets_redactable(subcommand_settings)
        except ValueError as error:
            sys.exit(emit_configuration_failure(tool, "auth", str(error)))
        _configure_process_logging(subcommand_settings)
        sys.exit((judge_main if judge else gate_main)(args))
    settings = load_settings()
    # The gates run before logging and before anything binds, so a misconfiguration is one clear
    # line on stderr and a non-zero exit, never a live unauthenticated server (ADR-0050).
    ensure_http_access_control(settings)
    ensure_secrets_redactable(settings)
    with contextlib.ExitStack() as listeners:
        sockets = http_serve_sockets(settings)
        for sock in sockets or ():
            listeners.enter_context(sock)
        _configure_process_logging(settings)
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
