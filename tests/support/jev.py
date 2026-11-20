"""An in-process fake provider and a helper that calls one tool through the real toolset."""

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar, cast, override

from mcp.types import CallToolResult, TextContent

from jev_judge_mcp.domain import JsonValue, Usage
from jev_judge_mcp.providers import NO_RETRIES, Evaluation, JevProvider, ProviderName
from jev_judge_mcp.redaction.values import Redactor
from jev_judge_mcp.security import SecurityGuard
from jev_judge_mcp.settings import Settings
from jev_judge_mcp.tools import TOOLS, Runtime, Toolset
from tests.support.next_call import assert_next_call

TEST_GUARD = SecurityGuard(Path("/nonexistent/jev-test-key"), Redactor(()))


def choice(selected: str, *labels: str) -> dict[str, object]:
    return {"choice": selected, "confidence": 1, "probabilities": {key: int(key == selected) for key in labels}}


NO_DEFECT = choice("none", "none", "wrong_result", "state_change", "error_handling", "safety_refusal")


class FakeProvider(JevProvider):
    """Returns `answers` as given, including values no JSON body could carry (NaN, infinity)."""

    name: ClassVar[ProviderName] = "compatible"
    label: ClassVar[str] = "Fake"

    def __init__(
        self,
        answers: Mapping[str, Any],
        *,
        request_id: str | None = None,
        error: BaseException | None = None,
    ) -> None:
        super().__init__(Redactor(()), retry=NO_RETRIES)
        self.answers = dict(answers)
        self.request_id = request_id
        self._error = error
        self.requests: list[tuple[JsonValue, dict[str, JsonValue]]] = []

    @override
    async def _send(
        self, state: JsonValue, questions: dict[str, JsonValue], model: str, timeout: float | None
    ) -> Evaluation:
        self.requests.append((state, questions))
        if self._error is not None:
            raise self._error
        return Evaluation(self.answers, Usage(1, 1), self.name, model, request_id=self.request_id)

    @override
    async def aclose(self) -> None:
        pass


@dataclass
class Outcome:
    payload: Any
    is_error: bool
    text: str
    requests: list[tuple[JsonValue, dict[str, JsonValue]]] = field(default_factory=list[Any])
    code: str | None = None


async def call_tool(
    name: str, arguments: Mapping[str, Any], answers: Mapping[str, Any], *, request_id: str | None = None
) -> Outcome:
    provider = FakeProvider(answers, request_id=request_id)
    toolset = Toolset(Runtime(Settings(), provider_factory=lambda _: provider), TOOLS)
    try:
        result = await toolset.call(name, arguments)
    finally:
        await toolset.aclose()
    text = cast(TextContent, result.content[0]).text
    try:
        payload = json.loads(text)
    except ValueError:
        payload = None
    code = _structured_code(result)
    if name in ("jev_gate", "jev_gate_range", "jev_review") and isinstance(payload, dict):
        assert_next_call(cast(dict[str, Any], payload))
    return Outcome(payload, bool(result.is_error), text, provider.requests, code)


def _structured_code(result: CallToolResult) -> str | None:
    content = cast(dict[str, object] | None, result.structured_content)
    raw = None if content is None else content.get("code")
    return raw if isinstance(raw, str) else None


def text_of(result: CallToolResult) -> str:
    return cast(TextContent, result.content[0]).text
