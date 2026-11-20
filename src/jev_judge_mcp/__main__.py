"""Console and python -m entry point for the CLI and MCP server."""

import io
import os
import sys
from collections.abc import Callable
from functools import partial
from importlib import import_module
from importlib.metadata import PackageNotFoundError
from typing import cast

from pydantic import ValidationError

from jev_judge_mcp.redaction.values import configure_process_logging
from jev_judge_mcp.security import build_guard
from jev_judge_mcp.settings import load_settings


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


def _print_version(_args: list[str]) -> None:
    from jev_judge_mcp.identity import DISTRIBUTION, reported_version

    try:
        identity = reported_version()
    except PackageNotFoundError:
        raise SystemExit(f"{DISTRIBUTION} is not installed; its version cannot be reported") from None
    print(f"{DISTRIBUTION} {identity}")


def _print_usage(_args: list[str]) -> None:
    print("""\
usage: jev-judge-mcp [<subcommand>]

With no arguments it serves MCP over stdio. JEV_MCP_TRANSPORT=streamable-http serves
Streamable HTTP instead (JEV_MCP_HTTP_PORT, JEV_MCP_HTTP_TOKEN).

subcommands:""")
    for names, _, help_line in COMMANDS:
        print(f"  {', '.join(names):19}{help_line}")


def _run_hook(completion: bool, args: list[str]) -> int:
    from jev_judge_mcp.hook import fail_open_or_ask, hook_required

    annotates = not completion and args[:1] in (["screen"], ["compact-cut"])
    required = not annotates and hook_required(os.environ)
    try:
        stdin = sys.stdin
        if isinstance(stdin, io.TextIOWrapper):
            stdin.reconfigure(encoding="utf-8", errors="strict")
        if not completion:
            from jev_judge_mcp.hook import main as hook_main

            return hook_main(args)
        from jev_judge_mcp.cli import completion_hook_main

        settings = load_settings()
        configure_process_logging(settings, build_guard(settings).redact)
        return completion_hook_main(args)
    except ValidationError as error:
        sys.stderr.write(_settings_error(error) + "\n")
        return fail_open_or_ask(required, "", "invalid settings")
    except Exception:
        return fail_open_or_ask(required, "jev-judge-mcp hook: unexpected error\n", "hook failed")


def _run_judgment(judge: bool, args: list[str]) -> int:
    from jev_judge_mcp.cli import emit_configuration_failure, gate_main, judge_main

    tool = (args[0] if args else "") if judge else "jev_gate"
    try:
        settings = load_settings()
    except ValidationError as error:
        return emit_configuration_failure(tool, "invalid_arguments", _settings_error(error))
    try:
        redact = build_guard(settings).redact
    except ValueError as error:
        return emit_configuration_failure(tool, "auth", str(error))
    configure_process_logging(settings, redact)
    return (judge_main if judge else gate_main)(args)


def _command(module: str, args: list[str]) -> int:
    run = cast("Callable[[list[str]], int]", import_module(f"jev_judge_mcp.{module}").main)
    return run(args)


COMMANDS: tuple[tuple[tuple[str, ...], Callable[[list[str]], int | None], str], ...] = (
    (("install",), partial(_command, "install.cli"), "install the server into a harness config"),
    (("hook",), partial(_run_hook, False), "run gate, screen, or compact-cut"),
    (("doctor",), partial(_command, "doctor"), "diagnose the local setup"),
    (("setup",), partial(_command, "setup"), "store the TypeSafe API key"),
    (("calibrate",), partial(_command, "calibrate"), "advisory threshold fitting on your labeled rows (offline)"),
    (("judge",), partial(_run_judgment, True), "call one tool; one JSON object on stdin"),
    (("gate",), partial(_run_judgment, False), "review a git range from local repo files"),
    (("completion-hook",), partial(_run_hook, True), "opt-in completion gate"),
    (("--version",), _print_version, "print the build identity"),
    (("--help", "-h"), _print_usage, "print this usage"),
)


def main() -> None:
    require_posix()
    name = sys.argv[1] if len(sys.argv) > 1 else ""
    try:
        for names, run, _ in COMMANDS:
            if name in names:
                status = run(sys.argv[2:])
                if status is not None:
                    raise SystemExit(status)
                return

        from jev_judge_mcp.server import main as serve_mcp

        serve_mcp()
    except ValidationError as error:
        raise SystemExit(_settings_error(error)) from None


if __name__ == "__main__":
    main()
