"""OpenRouter's Decisions API (`provider.ts:126-156`)."""

from typing import ClassVar

import httpx

from jev_judge_mcp.domain import JsonValue
from jev_judge_mcp.errors import Redactor
from jev_judge_mcp.providers.base import (
    Evaluation,
    HttpProvider,
    ProviderName,
    decode_body,
    parse_envelope,
)
from jev_judge_mcp.providers.retry import RetryPolicy

URL = "https://openrouter.ai/api/alpha/decisions"
LATEST = "jev-1.13"
"""OpenRouter has no redirecting `jev-latest` slug; the reference maps it to this release."""
_TITLE = "jev-mcp"
_REFERER = "https://github.com/PyModel/jev-judge-mcp"


def openrouter_slug(model: str) -> str:
    """`jev-latest` becomes `jev-1.13` first; then `typesafe/` is prefixed unless already there."""
    effective = LATEST if model == "jev-latest" else model
    return effective if effective.startswith("typesafe/") else f"typesafe/{effective}"


class OpenRouterProvider(HttpProvider):
    name: ClassVar[ProviderName] = "openrouter"
    label: ClassVar[str] = "OpenRouter decisions API"

    def __init__(
        self,
        redact: Redactor,
        *,
        api_key: str,
        client: httpx.AsyncClient | None = None,
        retry: RetryPolicy | None = None,
    ) -> None:
        super().__init__(redact, client, retry=retry)
        self._api_key = api_key

    async def _send(
        self, state: JsonValue, questions: dict[str, JsonValue], model: str, timeout: float | None
    ) -> Evaluation:
        slug = openrouter_slug(model)
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "HTTP-Referer": _REFERER,
            "X-Title": _TITLE,
            "X-OpenRouter-Title": _TITLE,
        }
        response = await self._post(URL, headers, {"model": slug, "state": state, "questions": questions})
        if not response.is_success:
            raise self._error(response)
        envelope = parse_envelope(decode_body(response.content), self.label)
        # The reference reports the slug it sent, never a model from the body (`provider.ts:154`).
        return Evaluation(envelope.answers, envelope.usage, self.name, slug)
