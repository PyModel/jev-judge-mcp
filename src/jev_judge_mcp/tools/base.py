"""What every tool shares: its published definition, the runtime it asks Jev through, and its result shape.

A tool validates its arguments against its published schema (`arguments.py`), builds state and
questions, asks Jev once at most, validates each answer, applies policy, and returns a payload that
is serialized as `JSON.stringify(payload, null, 2)` (ADR-0006).
"""

import logging
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any, cast

from mcp.types import Tool

from jev_judge_mcp import cache
from jev_judge_mcp.domain import JsonValue, Question
from jev_judge_mcp.extract.executor import RegexExecutor
from jev_judge_mcp.extract.worker import ProcessRegexExecutor
from jev_judge_mcp.policy import Action
from jev_judge_mcp.providers import Evaluation, JevProvider, resolve_model, resolve_provider
from jev_judge_mcp.settings import Settings
from jev_judge_mcp.telemetry import ACTIONS, Telemetry
from jev_judge_mcp.tools.arguments import Refinement
from jev_judge_mcp.tools.observed import worst_action
from jev_judge_mcp.validation.caps import CapScope

type Payload = Mapping[str, object]

logger = logging.getLogger("jev_judge_mcp.tools.base")


class ToolError(Exception):
    """A handler failure. Its message is the whole `isError` text, as a thrown `Error` is in the reference."""


@dataclass(frozen=True, slots=True)
class ToolResult:
    """A tool's reply: the payload to serialize, and whether the reference marks it `isError`.

    `action`, `item_actions` and `truncated` are for telemetry only, never serialized: the call's
    one headline auto/review/escalate Action (`None` when the payload carries none), the per-item
    Actions of a tool that judges items one by one, and the scopes of every cut the call's
    `CapLedger` made.
    """

    payload: Payload
    is_error: bool = False
    action: Action | None = None
    item_actions: tuple[Action, ...] = ()
    truncated: frozenset[CapScope] = frozenset()


def caller_actions(values: Iterable[object]) -> tuple[Action, ...]:
    """The auto/review/escalate values among `values`, for `ToolResult.item_actions`."""
    return tuple(value for value in values if value in ACTIONS)


def headline(item_actions: tuple[Action, ...]) -> Action | None:
    """A per-item tool's headline: its worst item Action, or `None` when no item carries one."""
    return worst_action(item_actions) if item_actions else None


def frame(
    tool: str, evaluation: Evaluation | None, body: Mapping[str, object], *, model: str | None = None
) -> dict[str, object]:
    """A success payload: `tool, model, provider`, then `body` in its own order, then `usage`.

    With no `evaluation` the call never asked Jev (jev_extract with no candidates): the caller names
    the configured `model`, the provider is `"none"`, and usage is `null`. The gate's `isError`
    refusal (`{tool, error}`) is not a success payload and is never framed.
    """
    if evaluation is None:
        return {"tool": tool, "model": model, "provider": "none", **body, "usage": None}
    framed: dict[str, object] = {
        "tool": tool,
        "model": evaluation.model,
        "provider": evaluation.provider,
        **body,
        "usage": evaluation.usage.to_wire(),
    }
    if evaluation.request_id:
        framed["request_id"] = evaluation.request_id
    return framed


class Runtime:
    """Per-server access to Jev: the configured model and a provider resolved on first use.

    Resolution waits for the first question, as the reference resolves inside `askJev`
    (`provider.ts:104-106`): a call that never asks (jev_extract with no candidates) never fails on
    provider configuration. A failed resolution raises again on every call that asks.
    """

    def __init__(
        self,
        settings: Settings,
        provider_factory: Callable[[Settings], JevProvider] = resolve_provider,
        regex_executor: RegexExecutor | None = None,
    ) -> None:
        self.settings = settings
        self.model = resolve_model(settings)
        self._provider_factory = provider_factory
        self._provider: JevProvider | None = None
        self._regex_executor = regex_executor or ProcessRegexExecutor()
        self.telemetry = Telemetry(payloads=settings.telemetry_payloads)

    @property
    def regex_executor(self) -> RegexExecutor:
        """Where jev_extract's patterns run: worker processes unless the server was built with another."""
        return self._regex_executor

    async def awarm(self) -> None:
        """Start the regex pool before the server takes calls (ADR-0058): a served pool never pays
        worker startup inside a call's deadline when a warm slot could have served it, and warming
        here overlaps no demand, so live workers never exceed the pool's size. Best effort: a pool
        that cannot start workers now tries again on demand, so the server still comes up for the
        tools that need no pool."""
        if not isinstance(self._regex_executor, ProcessRegexExecutor):
            return
        try:
            await self._regex_executor.warm()
        except Exception:
            logger.warning("regex pool did not warm; workers will start on demand", exc_info=True)

    async def ask(self, state: Payload, questions: Mapping[str, Question]) -> Evaluation:
        """Ask Jev `questions` about `state`. Raises `ProviderError` with redacted text.

        No whole-call deadline: the client's MCP cancellation (ADR-0011) stays the recovery path,
        while the provider's retry policy bounds every attempt and the whole bounded sequence
        (ADR-0057). The `jev.evaluate` span includes provider resolution, so a configuration error
        counts as a provider error. With `JEV_MCP_CACHE` on, an identical request replays the
        recorded evaluation instead of asking (ADR-0047); the replayed payload is byte-identical to
        the first answer's.
        """
        with self.telemetry.span("jev.evaluate", questions=len(questions)) as span:
            if self._provider is None:
                self._provider = self._provider_factory(self.settings)
            span.attributes["provider"] = self._provider.name
            wire_state = cast(JsonValue, state)
            cached = cache.lookup(self.settings, self._provider.name, self.model, wire_state, questions)
            if cached is not None:
                # A hit spent nothing, so it records no token attributes (the replayed payload's
                # usage stays verbatim); `tokens{direction}` counts what the provider was billed.
                span.attributes["cache"] = "hit"
                return cached
            evaluation = await self._provider.evaluate(wire_state, questions, self.model, None)
            cache.store(self.settings, self._provider.name, self.model, wire_state, questions, evaluation)
            span.attributes["input_tokens"] = evaluation.usage.input_tokens
            span.attributes["output_tokens"] = evaluation.usage.output_tokens
            return evaluation

    async def aclose(self) -> None:
        await self._regex_executor.aclose()
        if self._provider is not None:
            await self._provider.aclose()
            self._provider = None


type Handler = Callable[[dict[str, Any], Runtime], Awaitable[ToolResult]]


@dataclass(frozen=True, slots=True)
class JevTool:
    """A published tool: its `tools/list` definition, argument refinements, and handler."""

    definition: Tool
    handler: Handler
    refinements: Mapping[str, Refinement] = field(default_factory=dict[str, Refinement])

    @property
    def name(self) -> str:
        return self.definition.name


def define(name: str, title: str, description: str, input_schema: dict[str, Any]) -> Tool:
    """A snapshot-shaped definition: draft-07 schema and `execution.taskSupport: forbidden` (ADR-0010)."""
    return Tool.model_validate(
        {
            "name": name,
            "title": title,
            "description": description,
            "inputSchema": {**input_schema, "$schema": "http://json-schema.org/draft-07/schema#"},
            "execution": {"taskSupport": "forbidden"},
        }
    )
