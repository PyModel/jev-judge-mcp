"""TypeSafe's direct System One endpoint (`provider.ts:108-124`, ADR-0084)."""

from typing import ClassVar

import httpx

from jev_judge_mcp.domain import JsonValue
from jev_judge_mcp.providers.base import (
    Evaluation,
    HttpProvider,
    ProviderError,
    ProviderName,
    decode_body,
    parse_envelope,
)
from jev_judge_mcp.providers.retry import RetryPolicy
from jev_judge_mcp.redaction.values import Redactor

DEFAULT_BASE_URL = "https://api.typesafe.ai"
SYSTEM_ONE_PATH = "/v1/systemone"


class TypeSafeProvider(HttpProvider):
    name: ClassVar[ProviderName] = "typesafe"
    label: ClassVar[str] = "TypeSafe API"

    def __init__(
        self,
        redact: Redactor,
        *,
        api_key: str,
        base_url: str | None,
        client: httpx.AsyncClient | None = None,
        retry: RetryPolicy | None = None,
    ) -> None:
        super().__init__(redact, client, retry=retry, cache_scope=base_url or "")
        self._api_key = api_key.strip()
        self._base_url = DEFAULT_BASE_URL if base_url is None else base_url

    async def _send(
        self, state: JsonValue, questions: dict[str, JsonValue], model: str, timeout: float | None
    ) -> Evaluation:
        if not self._api_key or not self._api_key.isascii() or not self._api_key.isprintable() or " " in self._api_key:
            raise ProviderError(
                f"{self.label} request failed: API key must contain only printable ASCII without spaces."
            )
        response = await self._post(
            self._base_url.rstrip("/") + SYSTEM_ONE_PATH,
            {"Authorization": f"Bearer {self._api_key}", "Accept": "application/json"},
            {"state": state, "model": model, "questions": questions},
            timeout,
        )
        if not response.is_success:
            raise self._error(response)
        envelope = parse_envelope(decode_body(response.content), self.label, response.headers)
        # The reference reports the requested model (`provider.ts:122`).
        return Evaluation(envelope.answers, envelope.usage, self.name, model, request_id=envelope.request_id)
