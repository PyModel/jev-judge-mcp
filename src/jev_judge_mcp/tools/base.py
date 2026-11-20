"""What every tool shares: its published definition, the runtime it asks Jev through, and its result shape.

A tool validates its arguments against its published schema (`arguments.py`), builds state and
questions, asks Jev, validates each answer, applies policy, and returns a payload that
is serialized as `JSON.stringify(payload, null, 2)` (ADR-0006).
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable, Coroutine, Iterable, Mapping
from contextlib import nullcontext
from dataclasses import dataclass, field
from typing import Any, cast

import anyio
from mcp.types import Tool

from jev_judge_mcp import cache
from jev_judge_mcp.domain import JsonValue, Question
from jev_judge_mcp.extract.executor import RegexExecutor
from jev_judge_mcp.extract.worker import ProcessRegexExecutor
from jev_judge_mcp.policy import Action
from jev_judge_mcp.providers import (
    Evaluation,
    JevProvider,
    ProviderConfigError,
    ProviderError,
    ProviderTimeoutError,
    resolve_model,
    resolve_provider,
)
from jev_judge_mcp.settings import Settings
from jev_judge_mcp.telemetry import ACTIONS, Span, Telemetry
from jev_judge_mcp.tools.arguments import ArgumentsError, Refinement
from jev_judge_mcp.tools.observed import worst_action
from jev_judge_mcp.validation.caps import CapScope

type Payload = Mapping[str, object]

logger = logging.getLogger("jev_judge_mcp.tools.base")
_CLOSE_SECONDS = 10.0


class ToolError(Exception):
    """A handler failure. Its message is the whole `isError` text, as a thrown `Error` is in the reference.

    `code` is set at the raise site (`invalid_arguments`, `input_too_large`, or another code the
    refusal owns). The kernel does not recover it from the message (ADR-0062 amendment).
    """

    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


def code_of(error: BaseException) -> str:
    """The one error-code mapping. It reads the exception type and status, never the text.

    ProviderConfigError is `auth` for every provider, including a malformed OpenRouter key.
    A 401 is `auth` and a 429 is `quota` from `ProviderError.status` (ADR-0072). A 400 whose body
    carried `error_type` `max_tokens_exceeded` is `input_too_large`: the model's context window, which
    the caller fixes by splitting (ADR-0079). A timeout is
    `timeout`. Argument errors are `invalid_arguments`. A ToolError carries the code its raise
    site set. An unexpected handler error is `internal`.
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
    return "internal"


@dataclass(frozen=True, slots=True)
class ExtractHeadlines:
    """jev_extract's two sanctioned headlines, computed where the rows are (ADR-0064 amendment).

    `item_action` is the ADR-0013 headline: the worst row Action, or `None` when no row carries one
    (every field `not_found`, or only broken rows). `call_action` is the call headline the CLI
    reports: `not_found` is neutral, a broken row counts as `review`, and every field `not_found`
    settles as `auto`. `None` when a row status is outside that set.
    """

    item_action: Action | None
    call_action: str | None


@dataclass(frozen=True, slots=True)
class ToolResult:
    """A tool's reply: the payload to serialize, and whether the reference marks it `isError`.

    `action`, `item_actions` and `truncated` are for telemetry only, never serialized: the call's
    one headline auto/review/escalate Action (`None` when the payload carries none), the per-item
    Actions of a tool that judges items one by one, and the scopes of every cut the call's
    `CapLedger` made. `error_code` is set at an `isError` return site. `extract` is set only by
    jev_extract.
    """

    payload: Payload
    is_error: bool = False
    action: Action | None = None
    item_actions: tuple[Action, ...] = ()
    truncated: frozenset[CapScope] = frozenset()
    error_code: str | None = None
    extract: ExtractHeadlines | None = None
    call_action: str | None = None
    unresolved: bool | None = None

    @property
    def decision_action(self) -> str | None:
        if self.extract is not None:
            return self.extract.call_action
        return self.call_action if self.call_action is not None else self.action

    @property
    def decision_unresolved(self) -> bool:
        if self.unresolved is not None:
            return self.unresolved
        action = self.decision_action
        if action is not None:
            return action != "auto"
        return self.payload.get("status") != "ok"


def caller_actions(values: Iterable[object]) -> tuple[Action, ...]:
    """The auto/review/escalate values among `values`, for `ToolResult.item_actions`."""
    return tuple(value for value in values if value in ACTIONS)


def headline(item_actions: tuple[Action, ...]) -> Action | None:
    """A per-item tool's headline: its worst item Action, or `None` when no item carries one."""
    return worst_action(item_actions) if item_actions else None


def frame(
    tool: str,
    evaluation: Evaluation | None,
    body: Mapping[str, object],
    *,
    model: str | None = None,
    provider: str = "none",
) -> dict[str, object]:
    """A success payload: `tool, model, provider`, then `body` in its own order, then `usage`.

    Without an `evaluation`, the caller supplies the configured `model`. Usage is `null` and the
    provider defaults to `"none"`.
    """
    if evaluation is None:
        return {"tool": tool, "model": model, "provider": provider, **body, "usage": None}
    framed: dict[str, object] = {
        "tool": tool,
        "model": evaluation.model,
        "provider": evaluation.provider,
        **body,
        "usage": evaluation.usage,
    }
    if evaluation.request_id:
        framed["request_id"] = evaluation.request_id
    return framed


class _Pending:
    def __init__(self, evaluate: Callable[["_Pending"], Coroutine[Any, Any, Evaluation]]) -> None:
        self.subscribers = 0
        self.dispatched = False
        self.task = asyncio.create_task(evaluate(self))

    async def replay(self) -> Evaluation:
        self.subscribers += 1
        try:
            return await asyncio.shield(self.task)
        finally:
            self.subscribers -= 1
            if not self.subscribers and not self.dispatched and not self.task.done():
                self.task.cancel()


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
        *,
        tests_read_from_file: bool = False,
    ) -> None:
        self.settings = settings
        self.model = resolve_model(settings)
        self.tests_read_from_file = tests_read_from_file
        self.file_fanout = anyio.CapacityLimiter(8)
        self._provider_factory = provider_factory
        self._provider: JevProvider | None = None
        self._regex_executor = regex_executor or ProcessRegexExecutor()
        self.telemetry = Telemetry(payloads=settings.telemetry_payloads)
        self._pending: dict[bytes, _Pending] = {}
        self._cache_slots = anyio.Semaphore(256)
        self._cache_generation = 0
        self._close_lock = anyio.Lock()
        self._regex_closed = False
        self._max_inflight = settings.max_inflight
        self._inflight: anyio.Semaphore | None = None

    @property
    def provider_name(self) -> str:
        return self._provider.name if self._provider is not None else "none"

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
            if self._close_lock.locked():
                raise asyncio.CancelledError
            if self._provider is None:
                self._provider = self._provider_factory(self.settings)
            span.attributes["provider"] = self._provider.name
            wire_state = cast(JsonValue, state)
            provider = self._provider
            if self.settings.jev_judge_mcp_cache:
                return await self._cached(provider, wire_state, questions, span)
            return await self._evaluate_capped(provider, wire_state, questions, span)

    async def _cached(
        self, provider: JevProvider, state: JsonValue, questions: Mapping[str, Question], span: Span
    ) -> Evaluation:
        generation = self._cache_generation
        identity = (provider.name, provider.cache_scope, self.model, state, questions)
        key = bytes.fromhex(await anyio.to_thread.run_sync(cache.request_key, *identity))
        if generation != self._cache_generation:
            raise asyncio.CancelledError
        pending = self._pending.get(key)
        if pending is not None and not pending.task.cancelling():
            span.attributes["cache"] = "hit"
            return await pending.replay()
        await self._cache_slots.acquire()
        if generation != self._cache_generation:
            self._cache_slots.release()
            raise asyncio.CancelledError
        pending = self._pending.get(key)
        if pending is not None and not pending.task.cancelling():
            self._cache_slots.release()
            span.attributes["cache"] = "hit"
            return await pending.replay()

        async def evaluate(work: _Pending) -> Evaluation:
            cached = await cache.alookup(self.settings, *identity, digest=key)
            if cached is not None:
                span.attributes["cache"] = "hit"
                return cached
            evaluation = await self._evaluate_capped(provider, state, questions, span, work)
            await cache.astore(self.settings, *identity, evaluation, digest=key)
            return evaluation

        def completed(task: asyncio.Task[Evaluation]) -> None:
            if self._pending.get(key) is work:
                del self._pending[key]
            self._cache_slots.release()
            if not task.cancelled():
                task.exception()  # Observe failure even if every subscriber has disconnected.

        work = _Pending(evaluate)
        self._pending[key] = work
        work.task.add_done_callback(completed)
        return await work.replay()

    async def _evaluate_capped(
        self,
        provider: JevProvider,
        state: JsonValue,
        questions: Mapping[str, Question],
        span: Span,
        work: _Pending | None = None,
    ) -> Evaluation:
        """The provider call, behind the opt-in in-flight cap (ADR-0069); the default is no cap.

        The semaphore is created on first use, inside the running loop, so `Runtime` stays
        constructible outside one. A waiting call holds no provider resource and stays
        cancellable, like any other wait.
        """
        if self._max_inflight > 0 and self._inflight is None:
            self._inflight = anyio.Semaphore(self._max_inflight)
        capacity = self._inflight if self._inflight is not None else nullcontext()
        async with capacity:
            if work is not None:
                work.dispatched = True
            evaluation = await provider.evaluate(state, questions, self.model, None)
        if evaluation.usage.reported:
            span.attributes["input_tokens"] = evaluation.usage.input_tokens
            span.attributes["output_tokens"] = evaluation.usage.output_tokens
        return evaluation

    async def aclose(self) -> None:
        errors: list[BaseException] = []
        with anyio.CancelScope(shield=True):
            async with self._close_lock:
                self._cache_generation += 1
                pending = [item.task for item in self._pending.values()]
                for task in pending:
                    task.cancel()
                if pending:
                    _, unfinished = await asyncio.wait(pending, timeout=_CLOSE_SECONDS)
                    if unfinished:
                        errors.append(TimeoutError("cache evaluations did not stop"))
                if not self._regex_closed:
                    try:
                        with anyio.fail_after(_CLOSE_SECONDS):
                            await self._regex_executor.aclose()
                        self._regex_closed = True
                    except BaseException as error:
                        errors.append(error)
                if self._provider is not None:
                    try:
                        with anyio.fail_after(_CLOSE_SECONDS):
                            await self._provider.aclose()
                        self._provider = None
                    except BaseException as error:
                        errors.append(error)
        if len(errors) == 1:
            raise errors[0]
        if errors:
            raise BaseExceptionGroup("runtime shutdown failed", errors)
        await anyio.lowlevel.checkpoint_if_cancelled()


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
