"""A caller-configured Jev-compatible System One endpoint (`provider.ts:158-201`)."""

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
    refuse_credentials_in_url,
)
from jev_judge_mcp.providers.retry import RetryPolicy


class CompatibleProvider(HttpProvider):
    name: ClassVar[ProviderName] = "compatible"
    label: ClassVar[str] = "Jev-compatible endpoint"

    def __init__(
        self,
        redact: Redactor,
        *,
        api_key: str,
        base_url: str,
        client: httpx.AsyncClient | None = None,
        retry: RetryPolicy | None = None,
    ) -> None:
        super().__init__(redact, client, retry=retry)
        self._api_key = api_key
        self._base_url = base_url

    async def _send(
        self, state: JsonValue, questions: dict[str, JsonValue], model: str, timeout: float | None
    ) -> Evaluation:
        refuse_credentials_in_url(self._base_url, self.label)
        response = await self._post(
            self._base_url,
            {"Authorization": f"Bearer {self._api_key}"},
            {"model": model, "state": state, "questions": questions},
        )
        if not response.is_success:
            raise self._error(response)
        envelope = parse_envelope(decode_body(response.content), self.label)
        return Evaluation(envelope.answers, envelope.usage, self.name, envelope.model_or(model))
