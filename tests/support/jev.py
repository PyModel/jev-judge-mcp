"""An in-process fake provider and a helper that calls one tool through the real toolset."""

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, ClassVar, cast, override

from mcp.types import CallToolResult, TextContent

from jev_judge_mcp.domain import JsonValue, Usage
from jev_judge_mcp.errors import Redactor
from jev_judge_mcp.providers import Evaluation, JevProvider, ProviderName
from jev_judge_mcp.settings import Settings
from jev_judge_mcp.tools import TOOLS, Runtime, Toolset


class FakeProvider(JevProvider):
    """Returns `answers` as given, including values no JSON body could carry (NaN, infinity)."""

    name: ClassVar[ProviderName] = "compatible"
    label: ClassVar[str] = "Fake"

    def __init__(self, answers: Mapping[str, Any], *, request_id: str | None = None) -> None:
        super().__init__(Redactor(()))
        self.answers = dict(answers)
        self.request_id = request_id
        self.requests: list[tuple[JsonValue, dict[str, JsonValue]]] = []

    @override
    async def _send(
        self, state: JsonValue, questions: dict[str, JsonValue], model: str, timeout: float | None
    ) -> Evaluation:
        self.requests.append((state, questions))
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
    return Outcome(payload, bool(result.is_error), text, provider.requests)


def text_of(result: CallToolResult) -> str:
    return cast(TextContent, result.content[0]).text
