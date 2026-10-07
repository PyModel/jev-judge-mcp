"""`tools/call` dispatch with the reference's three error shapes (`mcp.js:100-142` in the TS SDK 1.30).

- An unknown tool or rejected arguments: `MCP error -32602: ...`, as an `isError` result.
- A handler failure (a thrown `Error` in the reference): its bare message, as an `isError` result.
- A handler's own error payload (jev_gate's evidence caps): the serialized payload with `isError`.

An argument or tool refusal logs one line — tool name, exception type, error code — because its
message is built from the caller's arguments (ids, paths) and argument text stays out of the log;
a `ProviderConfigError` is an ordinary configuration condition and logs one line with its text;
any other `Exception` is logged with a traceback before that result. `CancelledError` is not caught.
"""

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from mcp.types import CallToolResult, TextContent, Tool

from jev_judge_mcp.policy.actions import Action
from jev_judge_mcp.providers import ProviderConfigError, ProviderError, ProviderTimeoutError
from jev_judge_mcp.serialize import stringify, stringify_compact
from jev_judge_mcp.telemetry import ACTIONS, CAP_SCOPES, Span
from jev_judge_mcp.tools.arguments import INVALID_PARAMS, ArgumentParser, ArgumentsError, compile_argument_schema
from jev_judge_mcp.tools.base import ExtractHeadlines, JevTool, Runtime, ToolError, ToolResult
from jev_judge_mcp.validation.caps import CapScope

logger = logging.getLogger("jev_judge_mcp.telemetry")


@dataclass(frozen=True, slots=True)
class ToolOutcome:
    """What the kernel returns. MCP rendering is separate; callers do not parse `text` back.

    `payload` is the handler payload, or `None` when the error is a bare exception message
    (`text` is that message). `error_code` is set on errors and `None` on success.
    """

    payload: Mapping[str, object] | None
    text: str
    is_error: bool
    action: Action | None
    error_code: str | None
    item_actions: tuple[Action, ...] = ()
    truncated: frozenset[CapScope] = frozenset()
    extract: ExtractHeadlines | None = None


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
        """Dispatch under an `mcp.tool` span and render the outcome. MCP text stays byte-identical.

        `arguments` is `None` when the request carried none. Callers that need the typed outcome
        use `execute`; this method only renders it (ADR-0006, ADR-0062).
        """
        return render_call(await self.execute(name, arguments))

    async def execute(self, name: str, arguments: Mapping[str, object] | None) -> ToolOutcome:
        """The kernel: payload, the tool's Action, and the error code from the exception type.

        An unknown tool is labelled `unknown`: its name is caller text.
        """
        tool = self._tools.get(name)
        telemetry = self.runtime.telemetry
        with telemetry.span("mcp.tool", tool="unknown" if tool is None else name) as span:
            telemetry.payload(
                span, "arguments", lambda: "undefined" if arguments is None else stringify(dict(arguments))
            )
            outcome = await self._execute(name, tool, arguments, span)
            telemetry.payload(span, "result", lambda: outcome.text)
        return outcome

    async def _execute(
        self, name: str, tool: JevTool | None, arguments: Mapping[str, object] | None, span: Span
    ) -> ToolOutcome:
        if tool is None:
            span.attributes["outcome"] = "unknown_tool"
            return _error_outcome(f"MCP error {INVALID_PARAMS}: Tool {name} not found", "invalid_arguments")
        try:
            parsed = self._parsers[name](arguments)
            result = await tool.handler(parsed, self.runtime)
        except Exception as error:
            span.attributes["outcome"] = _outcome(error)
            if isinstance(error, ProviderConfigError):
                # An ordinary configuration condition (ADR-0007), not a defect: one line, no traceback.
                # Tool name only; argument text stays out of the log.
                logger.error("tool %s raised %s: %s", name, type(error).__name__, error)
            elif isinstance(error, (ArgumentsError, ToolError)):
                # A refusal the tool owns: its message quotes the caller's ids and paths, so only
                # the type and code are logged; the result carries the message to the caller.
                logger.error("tool %s raised %s (%s)", name, type(error).__name__, code_of(error))
            else:
                # Tool name only. The traceback is the diagnostic; argument text stays out of the log.
                logger.exception("tool %s raised", name)
            return _error_outcome(str(error), code_of(error))
        span.attributes["outcome"] = "error_payload" if result.is_error else "ok"
        for scope in CAP_SCOPES:
            span.attributes[f"truncated.{scope}"] = scope in result.truncated
        if result.action is not None:
            span.attributes["action"] = result.action
        for action in ACTIONS:
            span.attributes[f"item_actions.{action}"] = result.item_actions.count(action)
        return _from_result(result)

    async def aclose(self) -> None:
        logger.debug("metrics %s", self.runtime.telemetry.metrics.snapshot())
        await self.runtime.aclose()

    async def awarm(self) -> None:
        """The server's startup warm (ADR-0058): the pool is filled before any transport runs."""
        await self.runtime.awarm()


def code_of(error: BaseException) -> str:
    """The one error-code mapping. It reads the exception type and status, never the text.

    ProviderConfigError is `auth` for every provider, including a malformed OpenRouter key.
    A 401 is `auth` and a 429 is `quota` from `ProviderError.status` (ADR-0072). A 400 whose body
    carried `error_type` `max_tokens_exceeded` is `input_too_large`: the model's context window, which
    the caller fixes by splitting (ADR-0079). A timeout is
    `timeout`. Argument errors are `invalid_arguments`. A ToolError carries the code its raise
    site set. Everything else a provider raised is `provider`.
    """
    if isinstance(error, ProviderConfigError):
        return "auth"
    if isinstance(error, ProviderTimeoutError):
        return "timeout"
    if isinstance(error, ProviderError):
        if error.status == 401:
            return "auth"
        if error.status == 429:
            return "quota"
        if error.status == 400 and error.error_type == "max_tokens_exceeded":
            return "input_too_large"
        return "provider"
    if isinstance(error, ArgumentsError):
        return "invalid_arguments"
    if isinstance(error, ToolError):
        return error.code
    return "provider"


def render_call(outcome: ToolOutcome) -> CallToolResult:
    """MCP bytes for an outcome. The first block is `text`, unchanged (ADR-0006, ADR-0062)."""
    if not outcome.is_error:
        return CallToolResult(
            content=[TextContent(type="text", text=outcome.text)],
            structured_content=None,
            is_error=False,
        )
    code = outcome.error_code or "provider"
    return CallToolResult(
        content=[
            TextContent(type="text", text=outcome.text),
            TextContent(type="text", text=stringify_compact({"code": code})),
        ],
        structured_content={"code": code},
        is_error=True,
    )


def _outcome(error: Exception) -> str:
    if isinstance(error, ArgumentsError):
        return "arguments_error"
    if isinstance(error, ToolError):
        return "tool_error"
    if isinstance(error, ProviderError):
        return "provider_error"
    return "handler_error"


def _error_outcome(text: str, code: str) -> ToolOutcome:
    return ToolOutcome(payload=None, text=text, is_error=True, action=None, error_code=code)


def _from_result(result: ToolResult) -> ToolOutcome:
    text = stringify(result.payload)
    if result.is_error:
        return ToolOutcome(
            payload=result.payload,
            text=text,
            is_error=True,
            action=result.action,
            error_code=result.error_code or "provider",
            item_actions=result.item_actions,
            truncated=result.truncated,
            extract=result.extract,
        )
    return ToolOutcome(
        payload=result.payload,
        text=text,
        is_error=False,
        action=result.action,
        error_code=None,
        item_actions=result.item_actions,
        truncated=result.truncated,
        extract=result.extract,
    )
