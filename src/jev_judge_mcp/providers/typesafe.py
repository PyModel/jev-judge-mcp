"""TypeSafe direct through `typesafe-sdk` (`provider.ts:108-124`), the optional `typesafe` extra.

The SDK is imported on first use, so the server runs without it when another provider is configured.
The SDK itself never retries (`RetryPolicy(max_retries=0)`): one retry owner (ADR-0057) drives every
provider, so the SDK's attempts can never multiply jev's. Its typed response model is bypassed: the
raw body goes through the uniform envelope rules (ADR-0003) and answers stay raw for the tools to
validate.
"""

from typing import TYPE_CHECKING, Any, ClassVar, Self, cast, override

from pydantic import RootModel

from jev_judge_mcp.domain import JsonValue
from jev_judge_mcp.errors import Redactor
from jev_judge_mcp.providers.base import (
    Evaluation,
    JevProvider,
    ProviderError,
    ProviderName,
    decode_body,
    origin_of,
    parse_envelope,
    refuse_credentials_in_url,
)
from jev_judge_mcp.providers.retry import RetryPolicy
from jev_judge_mcp.serialize import stringify_compact

if TYPE_CHECKING:
    import httpx2
    from typesafe_sdk import AsyncTypeSafeClient


class _RawBody(RootModel[object]):
    """The response body as `JSON.parse` would read it: `None` when it is not JSON, NaN never a number."""

    @override
    @classmethod
    def model_validate_json(
        cls,
        json_data: str | bytes | bytearray,
        *,
        strict: bool | None = None,
        extra: Any = None,
        context: Any | None = None,
        by_alias: bool | None = None,
        by_name: bool | None = None,
    ) -> Self:
        content = json_data.encode() if isinstance(json_data, str) else bytes(json_data)
        return cls.model_construct(decode_body(content))


class TypeSafeProvider(JevProvider):
    name: ClassVar[ProviderName] = "typesafe"
    label: ClassVar[str] = "TypeSafe API"

    def __init__(
        self,
        redact: Redactor,
        *,
        api_key: str,
        base_url: str | None,
        transport: "httpx2.AsyncBaseTransport | None" = None,
        retry: RetryPolicy | None = None,
    ) -> None:
        super().__init__(redact, retry=retry)
        self._api_key = api_key
        self._base_url = base_url
        self._transport = transport
        self._client: AsyncTypeSafeClient | None = None
        self._wrapper: httpx2.AsyncClient | None = None
        # The allowed origin is fixed per instance: it comes from process config (ADR-0008), so the
        # lazy first-request assignment races only with itself, writing the same value.
        self._origin: tuple[str, str, int] | None = None

    async def _reject_cross_origin(self, request: "httpx2.Request") -> None:
        """ADR-0023 for the SDK transport too: a redirect may never leave the first request's origin.

        httpx2 does not follow redirects by default, so this hook is the guard for the day that
        default (or an injected `http_client`) changes: it refuses the hop before it is sent,
        exactly as `HttpProvider`'s event hook does for the raw providers.
        """
        origin = origin_of(str(request.url))
        if self._origin is None:
            self._origin = origin
        elif origin != self._origin:
            raise ProviderError(f"{self.label} request failed: a redirect left the configured origin and was blocked")

    def _sdk_client(self) -> "AsyncTypeSafeClient":
        if self._client is None:
            try:
                import httpx2
                from typesafe_sdk import AsyncTypeSafeClient
                from typesafe_sdk import RetryPolicy as SdkRetryPolicy
            except ImportError:
                raise ProviderError(
                    "The typesafe provider needs the typesafe-sdk package: install jev-judge-mcp[typesafe]."
                ) from None
            # The gate rides the wrapper the SDK will use, so an injected transport is gated too.
            self._wrapper = httpx2.AsyncClient(
                timeout=None,
                transport=self._transport,
                event_hooks={"request": [self._reject_cross_origin]},
            )
            self._client = AsyncTypeSafeClient(
                api_key=self._api_key,
                base_url=self._base_url,
                # The SDK never retries (ADR-0057): jev's policy is the one retry owner.
                retry=SdkRetryPolicy(max_retries=0),
                http_client=self._wrapper,
            )
        return self._client

    async def _send(
        self, state: JsonValue, questions: dict[str, JsonValue], model: str, timeout: float | None
    ) -> Evaluation:
        if self._base_url is not None:
            refuse_credentials_in_url(self._base_url, self.label)
        client = self._sdk_client()
        import httpx2
        from typesafe_sdk import TypeSafeAPIConnectionError, TypeSafeAPIError

        try:
            # The SDK's recursive JSON alias is partly unknown to pyright and narrower than JsonValue;
            # the wire is the same.
            response = await client.system_one(  # pyright: ignore[reportUnknownMemberType]
                cast(Any, state),
                cast(Any, questions),
                model=model,
                # `timeout` is this attempt's deadline from `evaluate` (ADR-0057). `None` must wait, as
                # fetch does, for a direct `_send` caller: the SDK reads a bare `None` as its 10 s default.
                timeout=httpx2.Timeout(None) if timeout is None else timeout,
                response_model=_RawBody,
            )
        except TypeSafeAPIError as error:
            # Only the 429 subclass carries `retry_after_ms`; other statuses have no hint.
            retry_after_ms = cast("float | None", getattr(error, "retry_after_ms", None))
            raise self._status_error(
                error.status,
                _body_text(error.body),
                retry_after=None if retry_after_ms is None else retry_after_ms / 1000,
            ) from None
        except TypeSafeAPIConnectionError as error:
            # The SDK writes `Connection error: {cause}`; a reset's cause has an empty message.
            cause = error.__cause__
            if isinstance(error, TimeoutError) or cause is None or str(cause):
                raise
            raise ProviderError(f"{self.label} request failed: {error}{type(cause).__name__}") from None
        envelope = parse_envelope(response.root, self.label)
        # The reference reports the requested model, never one from the body (`provider.ts:122`).
        return Evaluation(envelope.answers, envelope.usage, self.name, model)

    @override
    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
        if self._wrapper is not None:
            # The SDK may have closed it already; a second close is a no-op.
            await self._wrapper.aclose()


def _body_text(body: object) -> str:
    """The SDK hands over the error body parsed: text as text, JSON re-serialized, nothing as nothing."""
    if body is None:
        return ""
    if isinstance(body, str):
        return body
    return stringify_compact(body)
