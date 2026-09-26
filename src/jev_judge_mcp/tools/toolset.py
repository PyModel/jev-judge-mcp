"""`tools/call` dispatch with the reference's three error shapes (`mcp.js:100-142` in the TS SDK 1.30).

- An unknown tool or rejected arguments: `MCP error -32602: ...`, as an `isError` result.
- A handler failure (a thrown `Error` in the reference): its bare message, as an `isError` result.
- A handler's own error payload (jev_gate's evidence caps): the serialized payload with `isError`.

Owned failures and any other `Exception` are logged with a traceback before that result; a
`ProviderConfigError` is an ordinary configuration condition and logs one line instead.
`CancelledError` is not caught.
"""

import logging
from collections.abc import Mapping, Sequence

from mcp.types import CallToolResult, TextContent, Tool

from jev_judge_mcp.providers import ProviderConfigError, ProviderError
from jev_judge_mcp.responses import error_code
from jev_judge_mcp.serialize import stringify, stringify_compact
from jev_judge_mcp.telemetry import ACTIONS, CAP_SCOPES, Span
from jev_judge_mcp.tools.arguments import INVALID_PARAMS, ArgumentParser, ArgumentsError, compile_argument_schema
from jev_judge_mcp.tools.base import JevTool, Runtime, ToolError

logger = logging.getLogger("jev_judge_mcp.telemetry")


class Toolset:
    def __init__(self, runtime: Runtime, tools: Sequence[JevTool]) -> None:
        self._parsers: dict[str, ArgumentParser] = {
            tool.name: compile_argument_schema(tool.name, tool.definition.input_schema, tool.refinements)
            for tool in tools
        }
        self.runtime = runtime
        self._tools = {tool.name: tool for tool in tools}

    def definitions(self) -> list[Tool]:
        return [tool.definition for tool in self._tools.values()]

    def names(self) -> tuple[str, ...]:
        """Every callable tool name. `tools/list` publishes these verbatim (ADR-0013: one registry)."""
        return tuple(self._tools)

    async def call(self, name: str, arguments: Mapping[str, object] | None) -> CallToolResult:
        """Dispatch under an `mcp.tool` span. An unknown tool is labelled `unknown`: its name is caller text.

        `arguments` is `None` when the request carried none.
        """
        tool = self._tools.get(name)
        telemetry = self.runtime.telemetry
        with telemetry.span("mcp.tool", tool="unknown" if tool is None else name) as span:
            telemetry.payload(
                span, "arguments", lambda: "undefined" if arguments is None else stringify(dict(arguments))
            )
            result = await self._call(name, tool, arguments, span)
            telemetry.payload(span, "result", lambda: _text(result))
        return result

    async def _call(
        self, name: str, tool: JevTool | None, arguments: Mapping[str, object] | None, span: Span
    ) -> CallToolResult:
        if tool is None:
            span.attributes["outcome"] = "unknown_tool"
            return _error(f"MCP error {INVALID_PARAMS}: Tool {name} not found")
        try:
            parsed = self._parsers[name](arguments)
            result = await tool.handler(parsed, self.runtime)
        except Exception as error:
            span.attributes["outcome"] = _outcome(error)
            if isinstance(error, ProviderConfigError):
                # An ordinary configuration condition (ADR-0007), not a defect: one line, no traceback.
                # Tool name only; argument text stays out of the log.
                logger.error("tool %s raised %s: %s", name, type(error).__name__, error)
            else:
                # Tool name only. The traceback is the diagnostic; argument text stays out of the log.
                logger.exception("tool %s raised", name)
            return _error(str(error))
        span.attributes["outcome"] = "error_payload" if result.is_error else "ok"
        for scope in CAP_SCOPES:
            span.attributes[f"truncated.{scope}"] = scope in result.truncated
        if result.action is not None:
            span.attributes["action"] = result.action
        for action in ACTIONS:
            span.attributes[f"item_actions.{action}"] = result.item_actions.count(action)
        text = stringify(result.payload)
        if result.is_error:
            return _error(text)
        return CallToolResult(
            content=[TextContent(type="text", text=text)],
            structured_content=None,
            is_error=False,
        )

    async def aclose(self) -> None:
        logger.debug("metrics %s", self.runtime.telemetry.metrics.snapshot())
        await self.runtime.aclose()

    async def awarm(self) -> None:
        """The server's startup warm (ADR-0058): the pool is filled before any transport runs."""
        await self.runtime.awarm()


def _outcome(error: Exception) -> str:
    if isinstance(error, ArgumentsError):
        return "arguments_error"
    if isinstance(error, ToolError):
        return "tool_error"
    if isinstance(error, ProviderError):
        return "provider_error"
    return "handler_error"


def _text(result: CallToolResult) -> str:
    return "".join(block.text for block in result.content if isinstance(block, TextContent))


def _error(text: str) -> CallToolResult:
    """Error text stays byte-equal in the first block. The second block carries the code (ADR-0062)."""
    code = error_code(text)
    return CallToolResult(
        content=[
            TextContent(type="text", text=text),
            TextContent(type="text", text=stringify_compact({"code": code})),
        ],
        structured_content={"code": code},
        is_error=True,
    )
