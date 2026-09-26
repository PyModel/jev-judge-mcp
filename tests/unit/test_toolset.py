"""Handler failures reach stderr with a traceback, then come back as `isError`."""

import asyncio
import json
import logging
from collections.abc import Generator
from contextlib import contextmanager
from typing import Any, cast

import pytest
from mcp.types import CallToolResult, TextContent

from jev_judge_mcp.extract.executor import InProcessRegexExecutor
from jev_judge_mcp.providers import ProviderConfigError, ProviderError
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


def _code_block(result: CallToolResult) -> str:
    assert len(result.content) == 2
    block = result.content[1]
    assert isinstance(block, TextContent)
    return block.text


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


async def test_error_results_keep_the_text_and_append_the_code() -> None:
    """Clients that read only content still see the typed code. The first block stays the error text."""

    async def handler(_parsed: dict[str, Any], _runtime: Runtime) -> ToolResult:
        raise ToolError("No Jev provider credentials found. Set TYPESAFE_API_KEY.")

    toolset = _toolset(handler)
    try:
        failed = await toolset.call("boom", {"note": _NOTE})
        missing = await toolset.call("nope", {})

        async def ok(_parsed: dict[str, Any], _runtime: Runtime) -> ToolResult:
            return ToolResult({"ok": True})

        success_set = _toolset(ok)
        try:
            success = await success_set.call("boom", {"note": _NOTE})
        finally:
            await success_set.aclose()
    finally:
        await toolset.aclose()

    assert failed.is_error
    assert _text(failed) == "No Jev provider credentials found. Set TYPESAFE_API_KEY."
    assert json.loads(_code_block(failed)) == {"code": "auth"}
    assert failed.structured_content == {"code": "auth"}
    assert missing.is_error
    assert _text(missing).startswith("MCP error -32602")
    assert json.loads(_code_block(missing)) == {"code": "invalid_arguments"}
    assert missing.structured_content == {"code": "invalid_arguments"}
    assert not success.is_error
    assert len(success.content) == 1
    assert success.structured_content is None


@pytest.mark.parametrize(
    "text",
    [
        "Duplicate candidate id: a",
        "Duplicate item id: i",
        "Duplicate class id: class0",
        "Duplicate field id: f",
        'Candidate id "none" collides with an escape hatch; rename it or set escape_hatches: false.',
        "diff file list was not a list",
        "diff file list item was not an object",
        "Thresholds must satisfy 0 <= review_at <= auto_accept <= 1.",
    ],
    ids=[
        "duplicate-candidate",
        "duplicate-item",
        "duplicate-class",
        "duplicate-field",
        "escape-hatch-collision",
        "diff-not-a-list",
        "diff-item-not-an-object",
        "threshold-invariant",
    ],
)
async def test_caller_input_tool_errors_carry_invalid_arguments(text: str) -> None:
    """A refusal of the caller's own arguments is argument validation, not a provider failure."""

    async def handler(_parsed: dict[str, Any], _runtime: Runtime) -> ToolResult:
        raise ToolError(text)

    toolset = _toolset(handler)
    try:
        result = await toolset.call("boom", {"note": _NOTE})
    finally:
        await toolset.aclose()

    assert result.is_error
    assert _text(result) == text
    assert json.loads(_code_block(result)) == {"code": "invalid_arguments"}
    assert result.structured_content == {"code": "invalid_arguments"}


async def test_provider_config_error_logs_one_line_without_a_traceback(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A missing-credentials ProviderConfigError is an ordinary configuration condition (ADR-0007):
    the error text and its auth code are unchanged, and stderr carries one line, no traceback."""
    message = "No Jev provider credentials found. Set TYPESAFE_API_KEY, OPENROUTER_API_KEY (sk-or-)."

    async def handler(_parsed: dict[str, Any], _runtime: Runtime) -> ToolResult:
        raise ProviderConfigError(message)

    toolset = _toolset(handler)
    try:
        with _stderr([]):
            result = await toolset.call("boom", {"note": _NOTE})
        err = capsys.readouterr().err
    finally:
        await toolset.aclose()

    assert result.is_error
    assert _text(result) == message
    assert json.loads(_code_block(result)) == {"code": "auth"}
    assert toolset.runtime.telemetry.spans.spans[-1].attributes["outcome"] == "provider_error"
    lines = [line for line in err.splitlines() if line.strip()]
    assert len(lines) == 1
    assert "tool boom raised ProviderConfigError" in lines[0]
    assert "Traceback (most recent call last):" not in err
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
