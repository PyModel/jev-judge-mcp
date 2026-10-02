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
from tests.support.jev import call_tool

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
    ("error", "outcome", "traced"),
    [
        (ToolError(f"owned failure naming {_NOTE}", code="provider"), "tool_error", False),
        (ProviderError("provider down"), "provider_error", True),
    ],
)
async def test_owned_handler_errors_are_logged_then_returned(
    capsys: pytest.CaptureFixture[str], error: Exception, outcome: str, traced: bool
) -> None:
    """A provider failure logs its traceback (the diagnostic); a refusal the tool owns logs one
    line of type and code, because its message quotes the caller's arguments. Either way the
    caller gets the message, and the caller's text never reaches stderr."""

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
    assert ("Traceback (most recent call last):" in err) is traced
    assert "tool boom raised" in err
    if not traced:
        assert "ToolError (provider)" in err
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
    # One line, no traceback: the issue text names the caller's keys and values, and stays in the result.
    assert "Traceback (most recent call last):" not in err
    assert "tool boom raised ArgumentsError (invalid_arguments)" in err
    assert _NOTE not in err


async def test_error_results_keep_the_text_and_append_the_code() -> None:
    """Clients that read only content still see the typed code. The first block stays the error text."""

    async def handler(_parsed: dict[str, Any], _runtime: Runtime) -> ToolResult:
        raise ToolError("No Jev provider credentials found. Set TYPESAFE_API_KEY.", code="auth")

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


async def test_the_wire_code_is_the_raise_site_code_not_the_text() -> None:
    """A 401-shaped sentence stays `invalid_arguments` when the raise site set that code.

    The older texts in this test agreed with a text heuristic, so they still passed if parsing
    returned. This sentence does not.
    """
    text = "TypeSafe API 401: the code is the exception's, not this text."

    async def handler(_parsed: dict[str, Any], _runtime: Runtime) -> ToolResult:
        raise ToolError(text, code="invalid_arguments")

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


async def test_a_refusal_logs_its_type_and_code_but_not_the_callers_text(caplog: pytest.LogCaptureFixture) -> None:
    """A ToolError message quotes the caller's ids and paths; the log gets the type and code only."""
    with caplog.at_level(logging.ERROR, logger="jev_judge_mcp.telemetry"):
        outcome = await call_tool(
            "jev_file_judge",
            {"path": "../outside-marker.txt", "kind": "noul", "instructions": "q", "criteria": {}},
            {},
        )
    assert outcome.is_error and outcome.code == "path_outside_scope", outcome.text
    assert "outside-marker" in outcome.text  # the caller still gets the full message
    records = [record for record in caplog.records if record.name == "jev_judge_mcp.telemetry"]
    assert records and all("outside-marker" not in record.getMessage() for record in records)
    assert any("ToolError (path_outside_scope)" in record.getMessage() for record in records)
    assert all(record.exc_info is None for record in records)
