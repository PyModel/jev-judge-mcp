"""`Runtime` pins: the stdio product tier keeps the reference's no-deadline provider contract.

ADR-0021: `Runtime.ask` passes `timeout=None` on every provider request, exactly as the reference
sets none in `askJev`; the client's own MCP cancellation (ADR-0011) is the recovery path. If a
deadline is ever added, it is a Tier B decision with its own registry entry — this test fails
first.
"""

from typing import ClassVar, override

import pytest

from jev_judge_mcp.domain import JsonValue, NoulCriteria, NoulQuestion, Question, Usage
from jev_judge_mcp.errors import Redactor
from jev_judge_mcp.extract.executor import InProcessRegexExecutor
from jev_judge_mcp.extract.worker import ProcessRegexExecutor
from jev_judge_mcp.providers import Evaluation, JevProvider
from jev_judge_mcp.providers.base import ProviderName
from jev_judge_mcp.settings import Settings
from jev_judge_mcp.tools.base import Runtime

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class _RecordingProvider(JevProvider):
    """Records the timeout argument of every evaluate call; answers nothing real."""

    name: ClassVar[ProviderName] = "compatible"

    def __init__(self) -> None:
        super().__init__(Redactor(()))
        self.timeouts: list[float | None] = []

    @override
    async def _send(
        self, state: JsonValue, questions: dict[str, JsonValue], model: str, timeout: float | None
    ) -> Evaluation:
        self.timeouts.append(timeout)
        return Evaluation(answers={}, usage=Usage(), provider="compatible", model=model)

    @override
    async def aclose(self) -> None:
        return None


async def test_ask_passes_no_provider_deadline() -> None:
    provider = _RecordingProvider()
    runtime = Runtime(Settings(), provider_factory=lambda _: provider)
    questions: dict[str, Question] = {"q": NoulQuestion("Is this a question?", NoulCriteria("yes", "no"))}
    await runtime.ask({"state": True}, questions)
    assert provider.timeouts == [None]


async def test_default_regex_executor_is_the_process_pool() -> None:
    """jev_extract patterns run in killable workers by default; the in-process adapter is tests-only."""
    runtime = Runtime(Settings())
    try:
        assert isinstance(runtime.regex_executor, ProcessRegexExecutor)
        assert not isinstance(runtime.regex_executor, InProcessRegexExecutor)
    finally:
        await runtime.aclose()
