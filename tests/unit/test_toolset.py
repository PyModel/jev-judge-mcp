"""Handler failures reach stderr with a traceback, then come back as `isError`."""

import asyncio
import logging
from collections.abc import Generator
from contextlib import contextmanager
from typing import Any, cast

import pytest
from mcp.types import CallToolResult, TextContent

from jev_judge_mcp.extract.executor import InProcessRegexExecutor
from jev_judge_mcp.providers import ProviderError
from jev_judge_mcp.server import configure_logging
from jev_judge_mcp.settings import Settings
from jev_judge_mcp.tools.base import Handler, JevTool, Runtime, ToolError, ToolResult, define
from jev_judge_mcp.tools.toolset import Toolset

pytestmark = pytest.mark.anyio

_CONFIGURED = "fm-t1-fake-secret"
_NOTE = "caller-note-7f3a"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _toolset(handler: Handler) -> Toolset:
    definition = define(
        "boom",
        "Boom",
        "Raises on purpose.",
        {"type": "object", "properties": {"note": {"type": "string"}}, "additionalProperties": False},
    )
    runtime = Runtime(Settings(), regex_executor=InProcessRegexExecutor())
    return Toolset(runtime, [JevTool(definition=definition, handler=handler)])


@contextmanager
def _stderr(secrets: list[str]) -> Generator[None]:
    root = logging.getLogger()
    saved = (root.handlers[:], root.level)
    configure_logging("INFO", secrets)
    try:
        yield
    finally:
        root.handlers[:], root.level = saved


def _text(result: CallToolResult) -> str:
    return cast(TextContent, result.content[0]).text


async def test_handler_keyerror_reaches_stderr_and_is_an_error(capsys: pytest.CaptureFixture[str]) -> None:
    async def handler(_parsed: dict[str, Any], _runtime: Runtime) -> ToolResult:
        raise KeyError(f"missing {_CONFIGURED}")

    toolset = _toolset(handler)
    try:
        with _stderr([_CONFIGURED]):
            result = await toolset.call("boom", {"note": _NOTE})
        err = capsys.readouterr().err
    finally:
        await toolset.aclose()

    assert result.is_error
    assert _text(result) == str(KeyError(f"missing {_CONFIGURED}"))
    assert toolset.runtime.telemetry.spans.spans[-1].attributes["outcome"] == "handler_error"
    assert "Traceback (most recent call last):" in err
    assert "KeyError" in err
    assert "tool boom raised" in err
    assert "[redacted]" in err
    assert _CONFIGURED not in err
    assert _NOTE not in err


@pytest.mark.parametrize(
    ("error", "outcome"),
    [(ToolError("owned failure"), "tool_error"), (ProviderError("provider down"), "provider_error")],
)
async def test_owned_handler_errors_are_logged_then_returned(
    capsys: pytest.CaptureFixture[str], error: Exception, outcome: str
) -> None:
    async def handler(_parsed: dict[str, Any], _runtime: Runtime) -> ToolResult:
        raise error

    toolset = _toolset(handler)
    try:
        with _stderr([]):
            result = await toolset.call("boom", {"note": _NOTE})
        err = capsys.readouterr().err
    finally:
        await toolset.aclose()

    assert result.is_error
    assert _text(result) == str(error)
    assert toolset.runtime.telemetry.spans.spans[-1].attributes["outcome"] == outcome
    assert "Traceback (most recent call last):" in err
    assert "tool boom raised" in err
    assert _NOTE not in err


async def test_rejected_arguments_are_logged_then_returned(capsys: pytest.CaptureFixture[str]) -> None:
    async def handler(_parsed: dict[str, Any], _runtime: Runtime) -> ToolResult:
        raise AssertionError("the parser rejects this before the handler")

    toolset = _toolset(handler)
    try:
        with _stderr([]):
            result = await toolset.call("boom", {"note": 1, "leak": _NOTE})
        err = capsys.readouterr().err
    finally:
        await toolset.aclose()

    assert result.is_error
    assert "Invalid arguments for tool boom" in _text(result)
    assert toolset.runtime.telemetry.spans.spans[-1].attributes["outcome"] == "arguments_error"
    assert "Traceback (most recent call last):" in err
    assert "tool boom raised" in err
    assert _NOTE not in err


async def test_cancelled_error_is_not_caught(capsys: pytest.CaptureFixture[str]) -> None:
    async def handler(_parsed: dict[str, Any], _runtime: Runtime) -> ToolResult:
        raise asyncio.CancelledError

    toolset = _toolset(handler)
    try:
        with _stderr([]), pytest.raises(asyncio.CancelledError):
            await toolset.call("boom", {"note": _NOTE})
        err = capsys.readouterr().err
    finally:
        await toolset.aclose()

    assert "tool boom raised" not in err
    assert _NOTE not in err
