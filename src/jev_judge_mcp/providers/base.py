"""The provider contract, uniform envelope validation (ADR-0003), and the shared error path.

Every provider sends `{state, questions}` under a model name and returns an `Evaluation`. Adapters
differ only in URL, auth, model slug, the request/response envelope, and usage. Everything else is
here: the envelope rules the reference applies to `compatible` alone (`provider.ts:175-194`) apply to
every provider, every error message passes through secret redaction (ADR-0008), and a timeout or an
MCP cancellation aborts the in-flight request (ADR-0011).
"""

import logging
import math
from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass
from typing import ClassVar, Literal, override
from urllib.parse import urlsplit

import anyio
import httpx

from jev_judge_mcp.domain import JsonValue, Question, RawAnswer, Usage, as_number, decode_json, questions_to_wire
from jev_judge_mcp.errors import Redactor
from jev_judge_mcp.serialize import stringify_compact
from jev_judge_mcp.text import head

type ProviderName = Literal["typesafe", "openrouter", "cloudflare", "compatible"]

logger = logging.getLogger("jev_judge_mcp.providers")

ERROR_BODY_UNITS = 200
"""Error bodies are cut to `.slice(0, 200)` UTF-16 units (`provider.ts:146,172,247`)."""

_DEFAULT_PORTS = {"http": 80, "https": 443}


def origin_of(url: str | httpx.URL) -> tuple[str, str, int]:
    """The scheme/host/effective-port triple an origin comparison needs (ADR-0023).

    Shared by both HTTP transports: the raw providers' redirect hook and the typesafe-sdk wrapper's.
    """
    parsed = url if isinstance(url, httpx.URL) else httpx.URL(url)
    return (parsed.scheme, parsed.host or "", parsed.port or _DEFAULT_PORTS.get(parsed.scheme, 0))


class ProviderError(Exception):
    """A provider failure. Its text is MCP-visible, so `evaluate` redacts it before raising."""


class ProviderConfigError(ProviderError):
    """Provider resolution failed before any request (`provider.ts:35-77`)."""


class ProviderTimeoutError(ProviderError):
    """The request did not complete within the caller's timeout."""


@dataclass(frozen=True, slots=True)
class Evaluation:
    """One provider reply: raw answers for per-tool validation, plus usage and the model to report."""

    answers: dict[str, RawAnswer]
    usage: Usage
    provider: ProviderName
    model: str


@dataclass(frozen=True, slots=True)
class Envelope:
    """A reply envelope that passed `parse_envelope`. `model` is `None` when the body carried none."""

    answers: dict[str, RawAnswer]
    usage: Usage
    model: str | None

    def model_or(self, default: str) -> str:
        return default if self.model is None else self.model


def parse_envelope(body: object, label: str) -> Envelope:
    """Validate the envelope only, with the reference's `compatible` rules and messages (`provider.ts:175-194`).

    `usage` absent or null reports zeros; otherwise both counts must be finite non-negative numbers.
    `model` must be absent, null, or a string. Per-answer validity is each tool's job.
    """

    def invalid(why: str) -> ProviderError:
        return ProviderError(f"{label} returned an invalid response: {why}")

    if not isinstance(body, dict):
        raise invalid("expected a JSON object.")
    envelope: dict[str, object] = body  # pyright: ignore[reportUnknownVariableType]
    answers = envelope.get("answers")
    if not isinstance(answers, dict):
        raise invalid("expected an answers object.")
    usage = Usage()
    raw_usage = envelope.get("usage")
    if raw_usage is not None:
        counts = _usage_counts(raw_usage)
        if counts is None:
            raise invalid("usage must report finite non-negative input_tokens and output_tokens.")
        usage = Usage(*counts)
    model = envelope.get("model")
    if model is not None and not isinstance(model, str):
        raise invalid("model must be absent or a string.")
    return Envelope(answers=answers, usage=usage, model=model)  # pyright: ignore[reportUnknownArgumentType]


def _usage_counts(usage: object) -> tuple[int | float, int | float] | None:
    if not isinstance(usage, dict):
        return None
    record: dict[str, object] = usage  # pyright: ignore[reportUnknownVariableType]
    counts: list[int | float] = []
    for key in ("input_tokens", "output_tokens"):
        value = record.get(key)
        number = as_number(value)
        if number is None or not math.isfinite(number) or number < 0:
            return None
        counts.append(value if isinstance(value, int) else number)
    return counts[0], counts[1]


def decode_text(content: bytes) -> str:
    """`await response.text()`: UTF-8 with replacement characters, a leading BOM dropped."""
    return content.decode("utf-8", errors="replace").removeprefix("﻿")


def decode_body(content: bytes) -> object | None:
    """`await response.json().catch(() => null)`: `None` for a body `JSON.parse` would reject."""
    try:
        return decode_json(decode_text(content))
    except ValueError:
        return None


def refuse_credentials_in_url(url: str, label: str) -> None:
    """Node `fetch` refuses a URL with userinfo, so the reference fails there; failing is also safer here.

    `httpx` would turn the userinfo into Basic auth and overwrite the Bearer header. The text is
    V8's minus the URL, which redaction would blank anyway (ADR-0008).
    """
    if urlsplit(url).netloc.rpartition("@")[1]:
        raise ProviderError(
            f"{label} request failed: Request cannot be constructed from a URL that includes credentials"
        )


def encode_json(value: object) -> bytes:
    """`JSON.stringify(value)` as the request body."""
    return stringify_compact(value).encode("utf-8")


class JevProvider(ABC):
    """A transport to Jev. `evaluate` is the only entry point; adapters implement `_send`."""

    name: ClassVar[ProviderName]
    label: ClassVar[str]
    """Prefix of this provider's error messages."""

    def __init__(self, redact: Redactor) -> None:
        self._redact = redact

    async def evaluate(
        self, state: JsonValue, questions: Mapping[str, Question], model: str, timeout: float | None
    ) -> Evaluation:
        """Ask Jev `questions` about `state`. `timeout` bounds the whole call, in seconds; `None` waits.

        Raises `ProviderError` with redacted text. Cancellation of the calling task propagates and
        aborts the request (ADR-0011).
        """
        wire = questions_to_wire(questions)
        try:
            with anyio.fail_after(timeout):
                return await self._send(state, wire, model, timeout)
        except ProviderError as error:
            raise type(error)(self._redact(str(error))) from None
        except (TimeoutError, httpx.TimeoutException):
            after = "" if timeout is None else f" after {timeout:g} s"
            raise ProviderTimeoutError(f"{self.label} request timed out{after}.") from None
        except Exception as error:
            # The original may carry a secret-bearing URL or header; only the redacted text leaves.
            # Some carry no message at all (a reset is a bare `RemoteProtocolError`): name the type.
            cause = str(error) or type(error).__name__
            raise ProviderError(self._redact(f"{self.label} request failed: {cause}")) from None

    @abstractmethod
    async def _send(
        self, state: JsonValue, questions: dict[str, JsonValue], model: str, timeout: float | None
    ) -> Evaluation: ...

    def _status_error(self, status: int | str, body: str) -> ProviderError:
        """`{label} {status}: {body}`, the body redacted and then cut to 200 units (`provider.ts:172`).

        Redacting first means a secret straddling the cut cannot leak its head.
        """
        return ProviderError(f"{self.label} {status}: {head(self._redact(body), ERROR_BODY_UNITS)}")

    @abstractmethod
    async def aclose(self) -> None:
        """Release network resources."""


class HttpProvider(JevProvider):
    """A provider that POSTs JSON with `httpx`, as the reference does with `fetch` — except that a
    redirect may never leave the origin the first request used (ADR-0023): the reference's `fetch`
    would re-send the caller's state to a cross-origin 307/308 target, and the state is more
    sensitive than the credential (which httpx strips there anyway). httpx still builds every
    redirect request itself — method semantics and the redirect cap are httpx's, not ours; the
    hook below only refuses the hop before it is sent.
    """

    def __init__(self, redact: Redactor, client: httpx.AsyncClient | None = None) -> None:
        super().__init__(redact)
        # The allowed origin is fixed per instance: it comes from process config (ADR-0008), so the
        # lazy first-request assignment races only with itself, writing the same value.
        self._origin: tuple[str, str, int] | None = None
        # Like `fetch`: no timeout of its own (`evaluate` owns the deadline), and redirects are followed.
        self._client = client or httpx.AsyncClient(
            timeout=None,  # noqa: S113 - `evaluate` owns the deadline
            follow_redirects=True,
            event_hooks={"request": [self._reject_cross_origin]},
        )

    async def _reject_cross_origin(self, request: httpx.Request) -> None:
        """Async: the client awaits every request hook, redirects included."""
        origin = origin_of(request.url)
        if self._origin is None:
            self._origin = origin
        elif origin != self._origin:
            logger.warning("blocked a cross-origin redirect to %s://%s", request.url.scheme, request.url.host)
            raise ProviderError(f"{self.label} request failed: a redirect left the configured origin and was blocked")

    async def _post(self, url: str, headers: Mapping[str, str], body: object) -> httpx.Response:
        return await self._client.post(
            url, content=encode_json(body), headers={**headers, "Content-Type": "application/json"}
        )

    @override
    async def aclose(self) -> None:
        await self._client.aclose()
