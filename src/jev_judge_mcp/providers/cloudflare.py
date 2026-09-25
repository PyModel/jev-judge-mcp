"""Cloudflare Workers AI (`provider.ts:230-260`): the Jev contract inside `{model, input}` and the v4 envelope."""

from typing import ClassVar

import httpx

from jev_judge_mcp.domain import JsonValue, is_json_object
from jev_judge_mcp.errors import Redactor
from jev_judge_mcp.providers.base import Evaluation, HttpProvider, ProviderName, decode_body, parse_envelope
from jev_judge_mcp.providers.retry import RetryPolicy, retry_after_seconds
from jev_judge_mcp.serialize import stringify_compact


def cloudflare_slug(model: str) -> str:
    """A `typesafe/` model is kept verbatim; `jev-latest` is the single alias `typesafe/jev`."""
    if model.startswith("typesafe/"):
        return model
    return f"typesafe/{'jev' if model == 'jev-latest' else model}"


class CloudflareProvider(HttpProvider):
    name: ClassVar[ProviderName] = "cloudflare"
    label: ClassVar[str] = "Cloudflare AI run"

    def __init__(
        self,
        redact: Redactor,
        *,
        api_token: str,
        account_id: str,
        client: httpx.AsyncClient | None = None,
        retry: RetryPolicy | None = None,
    ) -> None:
        super().__init__(redact, client, retry=retry)
        self._api_token = api_token
        self._url = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run"

    async def _send(
        self, state: JsonValue, questions: dict[str, JsonValue], model: str, timeout: float | None
    ) -> Evaluation:
        slug = cloudflare_slug(model)
        response = await self._post(
            self._url,
            {"Authorization": f"Bearer {self._api_token}"},
            {"model": slug, "input": {"state": state, "questions": questions}},
        )
        # `.json().catch(() => ({}))`: an unparseable body is an empty object here, not null.
        parsed = decode_body(response.content)
        body: object = {} if parsed is None else parsed
        record: dict[str, object] = body if is_json_object(body) else {}
        errors = record.get("errors")
        if not response.is_success or record.get("success") is False:
            raise self._status_error(
                response.status_code,
                stringify_compact(body if errors is None else errors),
                retry_after=retry_after_seconds(response.headers),
            )
        # The v4 envelope double-nests: `result.result` holds the model output.
        outer = record.get("result")
        inner: object = None
        if is_json_object(outer):
            run_state = outer.get("state")
            # A missing state is not an error; only a string other than Completed is.
            if isinstance(run_state, str) and run_state != "Completed":
                raise self._status_error(f"state {run_state}", stringify_compact([] if errors is None else errors))
            inner = outer.get("result")
        payload = inner if inner is not None else outer if outer is not None else body
        # ADR-0003: the unwrapped payload gets the uniform envelope rules; the reference used `answers ?? {}`.
        envelope = parse_envelope(payload, self.label)
        return Evaluation(
            envelope.answers,
            envelope.usage,
            self.name,
            envelope.model_or(slug),
            request_id=envelope.request_id,
        )
