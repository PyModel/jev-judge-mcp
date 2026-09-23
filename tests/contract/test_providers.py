"""One provider contract, run against every provider (ROADMAP P4).

Adapters may differ only in URL, auth, model slug, the request/response envelope, and usage; each
`Case` below states exactly those differences, and every test runs unchanged against all four.
HTTP is mocked: respx for the `httpx` providers, an `httpx2.MockTransport` for `typesafe-sdk`,
which speaks `httpx2`. Cancellation (ADR-0011) has no reference counterpart and is Python-only.
"""

import json
from collections.abc import Awaitable, Callable, Iterator
from dataclasses import dataclass, field
from typing import Any

import anyio
import httpx
import httpx2
import pytest
import respx
from typesafe_sdk import RetryPolicy

from jev_judge_mcp.domain import ChoiceQuestion, NoulCriteria, NoulQuestion, Question, ScoreQuestion, Usage
from jev_judge_mcp.errors import REDACTED, Redactor
from jev_judge_mcp.providers import JevProvider, ProviderError, ProviderTimeoutError
from jev_judge_mcp.providers.cloudflare import CloudflareProvider, cloudflare_slug
from jev_judge_mcp.providers.compatible import CompatibleProvider
from jev_judge_mcp.providers.openrouter import OpenRouterProvider, openrouter_slug
from jev_judge_mcp.providers.typesafe import TypeSafeProvider
from tests.support.secrets import secret_env

pytestmark = pytest.mark.anyio

SECRETS = {
    "TYPESAFE_API_KEY": "ts-secret-0001",
    "TYPESAFE_BASE_URL": "https://ts-user:ts-pass-0001@typesafe.example",
    "OPENROUTER_API_KEY": "sk-or-secret-0001",
    "JEV_CLOUDFLARE_API_TOKEN": "jev-cf-secret-0001",
    "CLOUDFLARE_API_TOKEN": "cf-secret-0001",
    "AI_GATEWAY_API_KEY": "gw-secret-0001",
    "JEV_API_KEY": "jev-secret-0001",
    "JEV_API_BASE_URL": "https://jev-user:jev-pass-0001@jev.example/v1/systemone",
}
# ADR-0017: markers for any SecretStr field the hand list above has not grown to cover yet,
# so the redaction matrix covers every credential the schema declares, automatically.
EXTRA_MARKERS = tuple(value for name, value in secret_env().items() if name not in SECRETS)
REDACT = Redactor((*SECRETS.values(), *EXTRA_MARKERS))

STATE: dict[str, Any] = {"ticket": {"text": "I was charged twice.", "amount": 12.5}}
QUESTIONS: dict[str, Question] = {
    "billing": NoulQuestion("Is this about billing?", NoulCriteria("yes", "no")),
    "tone": ChoiceQuestion("What is the tone?", {"calm": None, "angry": "raised voice"}),
    "urgency": ScoreQuestion("How urgent?", ["low", "high"]),
}
WIRE_QUESTIONS = {name: question.to_wire() for name, question in QUESTIONS.items()}
# Raw answers pass through untouched: validity is each tool's job, not the provider's (ADR-0003).
ANSWERS: dict[str, Any] = {
    "billing": {"type": "noul", "noul": 0.9},
    "tone": {"type": "choice", "choice": "calm", "probabilities": {"calm": 0.7, "angry": 0.3}, "confidence": 0.4},
    "urgency": {"type": "score", "score": 7, "extra": [None]},
}

type Reply = tuple[int, bytes]
type Handler = Callable[[dict[str, Any]], Awaitable[Reply]]


@dataclass
class Sent:
    url: str
    headers: dict[str, str]
    body: Any


@dataclass
class Case:
    """What one provider is allowed to do differently."""

    id: str
    label: str
    url: str
    authorization: str
    make: Callable[["Wire"], JevProvider]
    request_body: Callable[[str], dict[str, Any]]
    """The JSON body sent for a requested model."""
    wrap: Callable[[Any], Any]
    """The success body that carries an inner envelope."""
    reported_model: Callable[[str, Any], str]
    """The model reported for a requested model and the body's `model`."""
    malformed_why: str
    extra_headers: dict[str, str] = field(default_factory=dict[str, str])


@dataclass
class Wire:
    """A fake HTTP peer shared by both mocking mechanisms."""

    handler: Handler
    sent: list[Sent] = field(default_factory=list[Sent])

    async def respond(self, url: str, headers: dict[str, str], content: bytes) -> Reply:
        body = json.loads(content)
        self.sent.append(Sent(url, headers, body))
        return await self.handler(body)

    def transport2(self) -> httpx2.MockTransport:
        async def handle(request: httpx2.Request) -> httpx2.Response:
            status, content = await self.respond(str(request.url), dict(request.headers), await request.aread())
            return httpx2.Response(status, content=content)

        return httpx2.MockTransport(handle)


OVERFLOW = "__1e400__"
"""Serialized as the bare literal `1e400`, which `JSON.parse` reads as Infinity.

Python's `inf` would serialize as `Infinity`, which `JSON.parse` rejects outright.
"""


def body_of(value: Any) -> bytes:
    return json.dumps(value).replace(f'"{OVERFLOW}"', "1e400").encode()


def reply(value: Any, status: int = 200) -> Handler:
    async def handler(_: dict[str, Any]) -> Reply:
        return status, value if isinstance(value, bytes) else body_of(value)

    return handler


def _compatible(wire: Wire) -> JevProvider:
    return CompatibleProvider(REDACT, api_key=SECRETS["JEV_API_KEY"], base_url="https://jev.example/v1/systemone")


def _typesafe(wire: Wire) -> JevProvider:
    # Retries off here: the reference-default policy is covered on its own below.
    return TypeSafeProvider(
        REDACT,
        api_key=SECRETS["TYPESAFE_API_KEY"],
        base_url="https://typesafe.example",
        transport=wire.transport2(),
        retry=RetryPolicy(max_retries=0),
    )


def _openrouter(wire: Wire) -> JevProvider:
    return OpenRouterProvider(REDACT, api_key=SECRETS["OPENROUTER_API_KEY"])


def _cloudflare(wire: Wire) -> JevProvider:
    return CloudflareProvider(REDACT, api_token=SECRETS["JEV_CLOUDFLARE_API_TOKEN"], account_id="acct-1")


def _flat(model: str) -> dict[str, Any]:
    return {"model": model, "state": STATE, "questions": WIRE_QUESTIONS}


CASES = [
    Case(
        id="compatible",
        label="Jev-compatible endpoint",
        url="https://jev.example/v1/systemone",
        authorization=f"Bearer {SECRETS['JEV_API_KEY']}",
        make=_compatible,
        request_body=_flat,
        wrap=lambda inner: inner,
        reported_model=lambda model, body_model: body_model if isinstance(body_model, str) else model,
        malformed_why="expected a JSON object.",
    ),
    Case(
        id="typesafe",
        label="TypeSafe API",
        url="https://typesafe.example/v1/systemone",
        authorization=f"Bearer {SECRETS['TYPESAFE_API_KEY']}",
        make=_typesafe,
        request_body=_flat,
        wrap=lambda inner: inner,
        reported_model=lambda model, _: model,
        malformed_why="expected a JSON object.",
    ),
    Case(
        id="openrouter",
        label="OpenRouter decisions API",
        url="https://openrouter.ai/api/alpha/decisions",
        authorization=f"Bearer {SECRETS['OPENROUTER_API_KEY']}",
        make=_openrouter,
        request_body=lambda model: _flat(openrouter_slug(model)),
        wrap=lambda inner: inner,
        reported_model=lambda model, _: openrouter_slug(model),
        malformed_why="expected a JSON object.",
        extra_headers={
            "http-referer": "https://github.com/PyModel/jev-judge-mcp",
            "x-title": "jev-mcp",
            "x-openrouter-title": "jev-mcp",
        },
    ),
    Case(
        id="cloudflare",
        label="Cloudflare AI run",
        url="https://api.cloudflare.com/client/v4/accounts/acct-1/ai/run",
        authorization=f"Bearer {SECRETS['JEV_CLOUDFLARE_API_TOKEN']}",
        make=_cloudflare,
        request_body=lambda model: {
            "model": cloudflare_slug(model),
            "input": {"state": STATE, "questions": WIRE_QUESTIONS},
        },
        wrap=lambda inner: {"result": {"state": "Completed", "result": inner}, "success": True, "errors": []},
        reported_model=lambda model, body_model: body_model if isinstance(body_model, str) else cloudflare_slug(model),
        # An unparseable body is `{}` in the reference, so the envelope finds no answers.
        malformed_why="expected an answers object.",
    ),
]


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(params=CASES, ids=[case.id for case in CASES])
def case(request: pytest.FixtureRequest) -> Case:
    return request.param


@pytest.fixture
def router() -> Iterator[respx.MockRouter]:
    with respx.mock(assert_all_called=False, assert_all_mocked=True) as mock:
        yield mock


class Peer:
    """Installs one `Wire` behind whichever mechanism the provider under test uses."""

    def __init__(self, case: Case, router: respx.MockRouter) -> None:
        self.case = case
        self.router = router

    def provider(self, handler: Handler) -> tuple[JevProvider, Wire]:
        wire = Wire(handler)

        async def side_effect(request: httpx.Request) -> httpx.Response:
            status, content = await wire.respond(str(request.url), dict(request.headers), request.content)
            return httpx.Response(status, content=content)

        self.router.post(self.case.url).mock(side_effect=side_effect)
        return self.case.make(wire), wire


@pytest.fixture
def peer(case: Case, router: respx.MockRouter) -> Peer:
    return Peer(case, router)


async def evaluate(provider: JevProvider, model: str = "jev-latest", timeout: float | None = 5) -> Any:
    try:
        return await provider.evaluate(STATE, QUESTIONS, model, timeout)
    finally:
        await provider.aclose()


async def failure(provider: JevProvider, model: str = "jev-latest", timeout: float | None = 5) -> str:
    with pytest.raises(ProviderError) as caught:
        await evaluate(provider, model, timeout)
    return str(caught.value)


async def test_success_passes_raw_answers_and_usage_through(case: Case, peer: Peer) -> None:
    body = case.wrap({"answers": ANSWERS, "usage": {"input_tokens": 321, "output_tokens": 17}})
    provider, wire = peer.provider(reply(body))

    evaluation = await evaluate(provider)

    assert evaluation.answers == ANSWERS
    assert list(evaluation.answers) == list(ANSWERS)
    assert evaluation.usage == Usage(321, 17)
    assert evaluation.provider == case.id
    assert evaluation.model == case.reported_model("jev-latest", None)
    [sent] = wire.sent
    assert sent.url == case.url
    assert sent.headers["authorization"] == case.authorization
    assert sent.headers["content-type"] == "application/json"
    assert sent.body == case.request_body("jev-latest")
    for name, value in case.extra_headers.items():
        assert sent.headers[name] == value


@pytest.mark.parametrize("usage", [None, "absent"], ids=["null", "absent"])
async def test_missing_usage_reports_zeros(case: Case, peer: Peer, usage: Any) -> None:
    inner: dict[str, Any] = {"answers": {}}
    if usage != "absent":
        inner["usage"] = usage
    provider, _ = peer.provider(reply(case.wrap(inner)))

    assert (await evaluate(provider)).usage == Usage(0, 0)


@pytest.mark.parametrize("content", [b"not json", b"", b'{"answers": NaN}', b"{"], ids=["text", "empty", "nan", "cut"])
async def test_malformed_body(case: Case, peer: Peer, content: bytes) -> None:
    provider, _ = peer.provider(reply(content))

    assert await failure(provider) == f"{case.label} returned an invalid response: {case.malformed_why}"


@pytest.mark.parametrize("inner", [[], "answers", 0, None], ids=["array", "string", "number", "null"])
async def test_non_object_body(case: Case, peer: Peer, inner: Any) -> None:
    provider, _ = peer.provider(reply(case.wrap(inner)))

    message = await failure(provider)

    # Cloudflare falls back from a null `result.result` to `result`, an object without answers.
    why = "expected an answers object." if case.id == "cloudflare" and inner is None else "expected a JSON object."
    assert message == f"{case.label} returned an invalid response: {why}"


@pytest.mark.parametrize(
    "answers", ["absent", None, [], "x", 0, True, [{"type": "noul"}]], ids=lambda v: f"answers={v!r}"
)
async def test_non_object_answers(case: Case, peer: Peer, answers: Any) -> None:
    inner: dict[str, Any] = {} if answers == "absent" else {"answers": answers}
    provider, _ = peer.provider(reply(case.wrap(inner)))

    assert await failure(provider) == f"{case.label} returned an invalid response: expected an answers object."


BAD_USAGE: list[Any] = [
    0,
    False,
    "10",
    [],
    {},
    {"input_tokens": 1},
    {"input_tokens": -1, "output_tokens": 1},
    {"input_tokens": 1, "output_tokens": "2"},
    {"input_tokens": True, "output_tokens": 1},
    {"input_tokens": 1, "output_tokens": None},
    {"input_tokens": OVERFLOW, "output_tokens": 1},
    {"input_tokens": 1, "output_tokens": 10**400},
]


@pytest.mark.parametrize("usage", BAD_USAGE, ids=lambda v: f"usage={str(v)[:40]}")
async def test_bad_usage(case: Case, peer: Peer, usage: Any) -> None:
    provider, _ = peer.provider(reply(case.wrap({"answers": {}, "usage": usage})))

    assert await failure(provider) == (
        f"{case.label} returned an invalid response: "
        "usage must report finite non-negative input_tokens and output_tokens."
    )


async def test_usage_accepts_zero_and_fractional_counts(case: Case, peer: Peer) -> None:
    provider, _ = peer.provider(reply(case.wrap({"answers": {}, "usage": {"input_tokens": 0, "output_tokens": 2.5}})))

    assert (await evaluate(provider)).usage == Usage(0, 2.5)


@pytest.mark.parametrize("model", [5, True, {}, ["m"]], ids=lambda v: f"model={v!r}")
async def test_non_string_model(case: Case, peer: Peer, model: Any) -> None:
    provider, _ = peer.provider(reply(case.wrap({"answers": {}, "model": model})))

    assert await failure(provider) == f"{case.label} returned an invalid response: model must be absent or a string."


@pytest.mark.parametrize("requested", ["jev-latest", "jev-1.12", "typesafe/jev-9", ""])
@pytest.mark.parametrize("body_model", [None, "jev-served-1"], ids=["no-body-model", "body-model"])
async def test_model_name_passthrough(case: Case, peer: Peer, requested: str, body_model: str | None) -> None:
    inner: dict[str, Any] = {"answers": {}, "model": body_model}
    provider, wire = peer.provider(reply(case.wrap(inner)))

    evaluation = await evaluate(provider, requested)

    assert wire.sent[0].body == case.request_body(requested)
    assert evaluation.model == case.reported_model(requested, body_model)


@pytest.mark.parametrize("status", [401, 403, 404, 422])
async def test_http_error_echoing_every_secret_is_redacted(case: Case, peer: Peer, status: int) -> None:
    echoed = {"error": f"denied for {case.authorization}", "seen": [*SECRETS.values(), *EXTRA_MARKERS]}
    provider, _ = peer.provider(reply(echoed, status))

    message = await failure(provider)

    assert message.startswith(f"{case.label} {status}: ")
    assert REDACTED in message
    assert_no_secret(message)


async def test_http_error_body_is_redacted_before_the_200_unit_cut(case: Case, peer: Peer) -> None:
    # The key straddles unit 200: cutting first would leave its head in the message.
    key = SECRETS["JEV_API_KEY"]
    text = "x" * (185 if case.id == "cloudflare" else 195) + key + "tail"  # Cloudflare prints `{"error":"` first
    provider, _ = peer.provider(reply({"error": text} if case.id == "cloudflare" else text.encode(), 500))

    message = await failure(provider)

    assert key[:5] not in message
    assert_no_secret(message)


async def test_connection_error_text_is_redacted(case: Case, peer: Peer) -> None:
    url = SECRETS["JEV_API_BASE_URL"]

    async def refuse(_: dict[str, Any]) -> Reply:
        detail = f"cannot reach {url} ({url.replace('jev.example', 'JEV.EXAMPLE')}/) with {case.authorization}"
        if case.id == "typesafe":
            raise httpx2.ConnectError(detail)
        raise httpx.ConnectError(detail)

    provider, _ = peer.provider(refuse)

    message = await failure(provider)

    assert message.startswith(f"{case.label} request failed: ")
    assert_no_secret(message)


async def test_connection_error_without_a_message_names_its_type(case: Case, peer: Peer) -> None:
    """A reset surfaces as httpx's `RemoteProtocolError` with an empty message; the cause is its type."""

    async def reset(_: dict[str, Any]) -> Reply:
        if case.id == "typesafe":
            raise httpx2.RemoteProtocolError("")
        raise httpx.RemoteProtocolError("")

    provider, _ = peer.provider(reset)

    sdk = "Connection error: " if case.id == "typesafe" else ""  # typesafe-sdk's own wrapper text
    assert await failure(provider) == f"{case.label} request failed: {sdk}RemoteProtocolError"


async def test_timeout_aborts_the_request(case: Case, peer: Peer) -> None:
    aborted = anyio.Event()

    async def stall(_: dict[str, Any]) -> Reply:
        try:
            await anyio.sleep(30)
        except anyio.get_cancelled_exc_class():
            aborted.set()
            raise
        return 200, b"{}"

    provider, _ = peer.provider(stall)

    with anyio.fail_after(5):
        with pytest.raises(ProviderTimeoutError) as caught:
            await evaluate(provider, timeout=0.05)

    assert str(caught.value) == f"{case.label} request timed out after 0.05 s."
    assert aborted.is_set()


async def test_client_cancel_aborts_the_request(case: Case, peer: Peer) -> None:
    """ADR-0011: cancelling the calling task (an MCP `notifications/cancelled`) aborts the request."""
    entered = anyio.Event()
    aborted = anyio.Event()
    results: list[object] = []

    async def stall(_: dict[str, Any]) -> Reply:
        entered.set()
        try:
            await anyio.sleep(30)
        except anyio.get_cancelled_exc_class():
            aborted.set()
            raise
        return 200, b"{}"

    provider, wire = peer.provider(stall)

    async def call() -> None:
        results.append(await provider.evaluate(STATE, QUESTIONS, "jev-latest", None))

    with anyio.fail_after(5):
        async with anyio.create_task_group() as tg:
            tg.start_soon(call)
            await entered.wait()
            tg.cancel_scope.cancel()
    await provider.aclose()

    assert aborted.is_set()
    assert results == []
    assert len(wire.sent) == 1


def assert_no_secret(message: str) -> None:
    for secret in (*SECRETS.values(), *EXTRA_MARKERS, "ts-pass-0001", "jev-pass-0001"):
        assert secret not in message, message


# Same-origin redirects (ADR-0023). The guard lives in HttpProvider, so one concrete provider pins
# it for all three httpx transports. httpx builds every redirect request; these tests pin that the
# blocked hop never leaves the process and that followed hops keep httpx's own semantics.

REDIRECT_ENVELOPE = {"answers": ANSWERS, "usage": {"input_tokens": 1, "output_tokens": 2}, "model": "m"}


def _redirect_provider(url: str) -> CompatibleProvider:
    return CompatibleProvider(REDACT, api_key="jev-secret-0001", base_url=url)


async def test_cross_origin_redirect_is_blocked_before_the_next_request_leaves(router: respx.MockRouter) -> None:
    first = router.post("https://jev.example/v1").mock(
        return_value=httpx.Response(307, headers={"Location": "https://evil.example/v1"})
    )
    away = router.post("https://evil.example/v1").mock(return_value=httpx.Response(200, json=REDIRECT_ENVELOPE))
    provider = _redirect_provider("https://jev.example/v1")
    with pytest.raises(ProviderError) as caught:
        await evaluate(provider, timeout=5)
    await provider.aclose()
    assert "origin" in str(caught.value)
    assert first.called
    assert not away.called


async def test_scheme_change_is_also_cross_origin(router: respx.MockRouter) -> None:
    router.post("http://jev.example/v1").mock(
        return_value=httpx.Response(308, headers={"Location": "https://jev.example/v1"})
    )
    provider = _redirect_provider("http://jev.example/v1")
    with pytest.raises(ProviderError) as caught:
        await evaluate(provider, timeout=5)
    await provider.aclose()
    assert "origin" in str(caught.value)


async def test_same_origin_307_308_replay_the_post_body(
    router: respx.MockRouter,
) -> None:
    for status in (307, 308):
        route = router.post("https://jev.example/v1").mock(
            return_value=httpx.Response(status, headers={"Location": "https://jev.example/v1/moved"}),
        )
        moved = router.post("https://jev.example/v1/moved").mock(
            return_value=httpx.Response(200, json=REDIRECT_ENVELOPE),
        )
        provider = _redirect_provider("https://jev.example/v1")
        result = await evaluate(provider, timeout=5)
        await provider.aclose()
        assert result.model == "m"
        assert moved.called
        assert route.calls.last.request.content == moved.calls.last.request.content


@pytest.mark.parametrize("status", [301, 302, 303])
async def test_permanent_and_temporary_redirects_become_get_on_the_same_path(
    router: respx.MockRouter, status: int
) -> None:
    router.post("https://jev.example/v1").mock(
        return_value=httpx.Response(status, headers={"Location": "https://jev.example/v1"})
    )
    asked = router.get("https://jev.example/v1").mock(return_value=httpx.Response(200, json=REDIRECT_ENVELOPE))
    provider = _redirect_provider("https://jev.example/v1")
    result = await evaluate(provider, timeout=5)
    await provider.aclose()
    assert result.model == "m"
    assert asked.called


async def test_a_relative_location_is_resolved_and_kept_on_origin(router: respx.MockRouter) -> None:
    router.post("https://jev.example/v1").mock(return_value=httpx.Response(307, headers={"Location": "/v1/elsewhere"}))
    moved = router.post("https://jev.example/v1/elsewhere").mock(
        return_value=httpx.Response(200, json=REDIRECT_ENVELOPE)
    )
    provider = _redirect_provider("https://jev.example/v1")
    result = await evaluate(provider, timeout=5)
    await provider.aclose()
    assert result.model == "m"
    assert moved.called


async def test_the_redirect_cap_still_applies_and_is_an_owned_error(router: respx.MockRouter) -> None:
    async def loop(_: httpx.Request) -> httpx.Response:
        return httpx.Response(308, headers={"Location": "https://jev.example/v1/loop"})

    router.post("https://jev.example/v1/loop").mock(side_effect=loop)
    provider = _redirect_provider("https://jev.example/v1/loop")
    with pytest.raises(ProviderError) as caught:
        await evaluate(provider, timeout=5)
    await provider.aclose()
    assert_no_secret(str(caught.value))
    assert "request failed" in str(caught.value)
