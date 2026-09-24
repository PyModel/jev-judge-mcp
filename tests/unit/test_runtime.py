"""`Runtime` pins: the stdio product tier passes no whole-call provider deadline.

ADR-0057: `Runtime.ask` still passes no whole-call deadline — the client's MCP cancellation
(ADR-0011) remains the recovery path for the call — while the provider's retry policy bounds every
attempt and the whole bounded sequence (registry entry `stdio-attempt-deadline`).
"""

from collections.abc import Mapping
from typing import ClassVar, override

import pytest

from jev_judge_mcp.domain import JsonValue, NoulCriteria, NoulQuestion, Question, Usage
from jev_judge_mcp.errors import Redactor
from jev_judge_mcp.extract.executor import InProcessRegexExecutor
from jev_judge_mcp.extract.worker import ProcessRegexExecutor
from jev_judge_mcp.providers import DEFAULT_RETRY_POLICY, Evaluation, JevProvider
from jev_judge_mcp.providers.base import ProviderName
from jev_judge_mcp.settings import Settings
from jev_judge_mcp.tools.base import Runtime

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class _RecordingProvider(JevProvider):
    """Records every evaluate call's whole-call timeout and every attempt's deadline; answers nothing real."""

    name: ClassVar[ProviderName] = "compatible"

    def __init__(self) -> None:
        super().__init__(Redactor(()))
        self.call_timeouts: list[float | None] = []
        self.attempt_timeouts: list[float | None] = []

    @override
    async def evaluate(
        self, state: JsonValue, questions: Mapping[str, Question], model: str, timeout: float | None
    ) -> Evaluation:
        self.call_timeouts.append(timeout)
        return await super().evaluate(state, questions, model, timeout)

    @override
    async def _send(
        self, state: JsonValue, questions: dict[str, JsonValue], model: str, timeout: float | None
    ) -> Evaluation:
        self.attempt_timeouts.append(timeout)
        return Evaluation(answers={}, usage=Usage(), provider="compatible", model=model)

    @override
    async def aclose(self) -> None:
        return None


async def test_ask_passes_no_whole_call_deadline() -> None:
    """ADR-0057: the caller's argument stays `None` (cancellation is the whole-call recovery path),
    while each attempt is handed the retry policy's per-attempt deadline."""
    provider = _RecordingProvider()
    runtime = Runtime(Settings(), provider_factory=lambda _: provider)
    questions: dict[str, Question] = {"q": NoulQuestion("Is this a question?", NoulCriteria("yes", "no"))}
    await runtime.ask({"state": True}, questions)
    assert provider.call_timeouts == [None]
    assert provider.attempt_timeouts == [DEFAULT_RETRY_POLICY.per_attempt_timeout]


async def test_default_regex_executor_is_the_process_pool() -> None:
    """jev_extract patterns run in killable workers by default; the in-process adapter is tests-only."""
    runtime = Runtime(Settings())
    try:
        assert isinstance(runtime.regex_executor, ProcessRegexExecutor)
        assert not isinstance(runtime.regex_executor, InProcessRegexExecutor)
    finally:
        await runtime.aclose()
