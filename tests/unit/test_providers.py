"""Provider behavior outside the shared contract: slugs, Cloudflare's v4 envelope, TypeSafe's SDK, redaction."""

import io
import json
import logging
import subprocess
import sys
from typing import Any

import httpx
import httpx2
import pytest
import respx

from jev_judge_mcp.domain import NoulCriteria, NoulQuestion, Usage
from jev_judge_mcp.errors import REDACTED, RedactingFilter, Redactor
from jev_judge_mcp.providers import NO_RETRIES, Evaluation, ProviderError, RetryPolicy
from jev_judge_mcp.providers import retry as retry_timing
from jev_judge_mcp.providers.base import decode_body, parse_envelope
from jev_judge_mcp.providers.cloudflare import CloudflareProvider, cloudflare_slug
from jev_judge_mcp.providers.compatible import CompatibleProvider
from jev_judge_mcp.providers.openrouter import openrouter_slug
from jev_judge_mcp.providers.typesafe import TypeSafeProvider
from jev_judge_mcp.serialize import stringify_compact
from jev_judge_mcp.server import configure_logging
from jev_judge_mcp.text import head

QUESTIONS = {"q": NoulQuestion("Is it?", NoulCriteria("yes", "no"))}
CF_URL = "https://api.cloudflare.com/client/v4/accounts/acct/ai/run"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.parametrize(
    ("model", "slug"),
    [
        ("jev-latest", "typesafe/jev-1.13"),
        ("jev-1.12", "typesafe/jev-1.12"),
        ("typesafe/jev-1.9", "typesafe/jev-1.9"),
        ("typesafe/jev-latest", "typesafe/jev-latest"),
        ("", "typesafe/"),
    ],
)
def test_openrouter_slug(model: str, slug: str) -> None:
    assert openrouter_slug(model) == slug


@pytest.mark.parametrize(
    ("model", "slug"),
    [
        ("jev-latest", "typesafe/jev"),
        ("jev-1.12", "typesafe/jev-1.12"),
        ("typesafe/jev-latest", "typesafe/jev-latest"),
        ("", "typesafe/"),
    ],
)
def test_cloudflare_slug(model: str, slug: str) -> None:
    assert cloudflare_slug(model) == slug


# --- Cloudflare's v4 envelope (`provider.ts:245-259`) ---


async def cloudflare(status: int, body: Any) -> Evaluation:
    provider = CloudflareProvider(Redactor(["cf-token"]), api_token="cf-token", account_id="acct", retry=NO_RETRIES)  # noqa: S106
    content = body if isinstance(body, bytes) else json.dumps(body).encode()
    with respx.mock(assert_all_mocked=True) as router:
        router.post(CF_URL).mock(return_value=httpx.Response(status, content=content))
        try:
            return await provider.evaluate("state", QUESTIONS, "jev-latest", 5)
        finally:
            await provider.aclose()


async def cloudflare_error(status: int, body: Any) -> str:
    with pytest.raises(ProviderError) as caught:
        await cloudflare(status, body)
    return str(caught.value)


@pytest.mark.anyio
@pytest.mark.parametrize(
    "result",
    [
        {"state": "Completed", "result": {"answers": {"q": 1}}},
        {"result": {"answers": {"q": 1}}},  # a missing state is not an error
        {"state": 7, "result": {"answers": {"q": 1}}},  # nor is a non-string one
        {"answers": {"q": 1}},  # no inner result: the outer object is the payload
    ],
    ids=["completed", "no-state", "numeric-state", "single-nested"],
)
async def test_cloudflare_unwraps_result(result: dict[str, Any]) -> None:
    evaluation = await cloudflare(200, {"result": result, "success": True})
    assert evaluation.answers == {"q": 1}
    assert evaluation.model == "typesafe/jev"


@pytest.mark.anyio
async def test_cloudflare_top_level_payload_without_result() -> None:
    evaluation = await cloudflare(200, {"answers": {"q": 1}, "usage": {"input_tokens": 3, "output_tokens": 4}})
    assert evaluation.usage == Usage(3, 4)


@pytest.mark.anyio
async def test_cloudflare_state_other_than_completed_is_an_error() -> None:
    body: dict[str, Any] = {
        "result": {"state": "Running", "result": {"answers": {}}},
        "errors": [{"code": 1, "message": "busy"}],
    }
    assert await cloudflare_error(200, body) == 'Cloudflare AI run state Running: [{"code":1,"message":"busy"}]'


@pytest.mark.anyio
async def test_cloudflare_state_echoing_a_secret_is_redacted() -> None:
    """The state string is body text outside the error-body cut; the final redaction pass covers it."""
    body = {"result": {"state": "Denied cf-token"}}
    assert await cloudflare_error(200, body) == "Cloudflare AI run state Denied [redacted]: []"


@pytest.mark.anyio
async def test_cloudflare_state_error_without_errors_prints_an_empty_list() -> None:
    assert await cloudflare_error(200, {"result": {"state": "Failed"}}) == "Cloudflare AI run state Failed: []"


@pytest.mark.anyio
async def test_cloudflare_success_false_on_200_is_an_error() -> None:
    body: dict[str, Any] = {"success": False, "errors": [{"message": "bad token cf-token"}], "result": {"answers": {}}}
    assert await cloudflare_error(200, body) == 'Cloudflare AI run 200: [{"message":"bad token [redacted]"}]'


@pytest.mark.anyio
async def test_cloudflare_http_error_without_errors_prints_the_body() -> None:
    assert await cloudflare_error(500, {"oops": True}) == 'Cloudflare AI run 500: {"oops":true}'


@pytest.mark.anyio
async def test_cloudflare_http_error_with_unparseable_body_prints_an_empty_object() -> None:
    assert await cloudflare_error(502, b"<html>bad gateway</html>") == "Cloudflare AI run 502: {}"


# --- TypeSafe through typesafe-sdk ---


def typesafe(handler: Any, retry: RetryPolicy | None = None) -> TypeSafeProvider:
    """A TypeSafe provider over a mock transport; `retry` is jev's policy (ADR-0057)."""
    return TypeSafeProvider(
        Redactor(["typesafe-test-key"]),
        api_key="typesafe-test-key",
        base_url="https://ts.example",
        transport=httpx2.MockTransport(handler),
        retry=retry,
    )


@pytest.fixture
def fast_retries(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Offline, deterministic retries: no real sleeping, full (unjittered) backoff, delays recorded."""
    delays: list[float] = []

    async def instant(seconds: float) -> None:
        delays.append(seconds)

    monkeypatch.setattr(retry_timing, "sleep", instant)
    monkeypatch.setattr(retry_timing, "uniform", lambda: 0.0)
    return delays


@pytest.mark.anyio
async def test_typesafe_sdk_retries_are_disabled(fast_retries: list[float]) -> None:
    """One retry owner (ADR-0057): the SDK is built with `max_retries=0`, so a 503 is attempted
    exactly jev policy's three times — never multiplied by the SDK's own default two retries."""
    statuses = [503, 503, 200]

    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(statuses.pop(0), json={"answers": {"q": 0.5}}, headers={"retry-after-ms": "0"})

    provider = typesafe(handler)
    try:
        evaluation = await provider.evaluate("state", QUESTIONS, "jev-latest", 5)
    finally:
        await provider.aclose()

    assert statuses == []
    assert evaluation.answers == {"q": 0.5}
    assert fast_retries == [0.5, 1.0]


@pytest.mark.anyio
async def test_typesafe_error_body_as_text() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(400, text="bad key typesafe-test-key")

    provider = typesafe(handler, NO_RETRIES)
    with pytest.raises(ProviderError) as caught:
        await provider.evaluate("state", QUESTIONS, "jev-latest", 5)
    await provider.aclose()

    assert str(caught.value) == "TypeSafe API 400: bad key [redacted]"


@pytest.mark.anyio
async def test_typesafe_error_without_body() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(404)

    provider = typesafe(handler, NO_RETRIES)
    with pytest.raises(ProviderError) as caught:
        await provider.evaluate("state", QUESTIONS, "jev-latest", 5)
    await provider.aclose()

    assert str(caught.value) == "TypeSafe API 404: "


@pytest.mark.anyio
async def test_typesafe_invalid_api_key_is_a_provider_error() -> None:
    provider = TypeSafeProvider(
        Redactor(["bad-test-key"]),
        api_key="bad-test-key",
        base_url=None,
        transport=httpx2.MockTransport(lambda _: httpx2.Response(401, json={"error": "unauthorized"})),
    )
    with pytest.raises(ProviderError) as caught:
        await provider.evaluate("state", QUESTIONS, "jev-latest", 5)
    await provider.aclose()

    assert str(caught.value).startswith("TypeSafe API 401: ")


@pytest.mark.anyio
async def test_typesafe_without_the_sdk_installed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "typesafe_sdk", None)
    provider = TypeSafeProvider(Redactor([]), api_key="typesafe-test-key", base_url=None)

    with pytest.raises(ProviderError) as caught:
        await provider.evaluate("state", QUESTIONS, "jev-latest", 5)

    assert str(caught.value) == (
        "The typesafe provider needs the typesafe-sdk package: install jev-judge-mcp[typesafe]."
    )


def test_typesafe_sdk_is_imported_lazily() -> None:
    script = (
        "import sys, os\n"
        "os.environ.update(TYPESAFE_API_KEY='typesafe-test-key')\n"
        "from jev_judge_mcp.providers import resolve_provider\n"
        "from jev_judge_mcp.server import build_server\n"
        "from jev_judge_mcp.settings import load_settings\n"
        "provider = resolve_provider(load_settings())\n"
        "assert type(provider).__name__ == 'TypeSafeProvider'\n"
        "assert 'typesafe_sdk' not in sys.modules, 'imported eagerly'\n"
    )
    subprocess.run([sys.executable, "-c", script], check=True)


# --- Envelope parsing, JSON decoding ---


@pytest.mark.parametrize(
    ("content", "parsed"),
    [
        (b'{"a": 1}', {"a": 1}),
        (b'\xef\xbb\xbf{"a": 1}', {"a": 1}),  # TextDecoder drops the BOM
        (b"null", None),
        (b"", None),
        (b"NaN", None),
        (b'{"a": -Infinity}', None),
        (b'{"a": 1e400}', {"a": float("inf")}),
        (b"[" * 100_000, None),
    ],
    ids=["object", "bom", "null", "empty", "nan", "infinity", "overflow", "deep"],
)
def test_decode_body_follows_json_parse(content: bytes, parsed: object) -> None:
    assert decode_body(content) == parsed


def test_envelope_keeps_answers_raw_and_in_order() -> None:
    answers = {"b": None, "a": [1, "x"], "c": {"noul": float("nan")}}
    envelope = parse_envelope({"answers": answers, "model": None, "usage": None}, "X")
    assert envelope.answers is answers
    assert list(envelope.answers) == ["b", "a", "c"]
    assert envelope.model is None
    assert envelope.model_or("m") == "m"


# --- Redaction (ADR-0008) ---


def test_redactor_replaces_every_occurrence_longest_first() -> None:
    redact = Redactor(["fake-key", "fake-key-extension", ""])
    assert redact("Bearer fake-key-extension / fake-key / fake-keyfake-key") == (
        f"Bearer {REDACTED} / {REDACTED} / {REDACTED}{REDACTED}"
    )


def test_redactor_covers_url_userinfo_in_any_normalized_form() -> None:
    redact = Redactor(["https://user:p%40ss@Jev.Example:8443/v1"])
    text = "POST https://user:p%40ss@jev.example:8443/v1/ failed; auth user:p%40ss; password p%40ss"
    assert "p%40ss" not in redact(text)
    assert "user:p%40ss" not in redact(text)


def test_redactor_leaves_urls_without_userinfo_alone_except_whole() -> None:
    redact = Redactor(["https://jev.example/v1"])
    assert redact("https://jev.example/v1 and https://jev.example/") == f"{REDACTED} and https://jev.example/"


def test_redacting_filter_covers_message_args_and_traceback() -> None:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.addFilter(RedactingFilter(Redactor(["s3cret-value"])))
    logger = logging.getLogger("tests.redaction")
    logger.addHandler(handler)
    logger.propagate = False
    try:
        logger.warning("key %s in %r", "s3cret-value", {"k": "s3cret-value"})
        try:
            raise RuntimeError("boom s3cret-value")
        except RuntimeError:
            logger.exception("failed with s3cret-value")
    finally:
        logger.removeHandler(handler)

    output = stream.getvalue()
    assert "s3cret-value" not in output
    assert "key [redacted] in {'k': '[redacted]'}" in output
    assert "RuntimeError: boom [redacted]" in output


def test_server_logging_redacts_configured_secrets(capsys: pytest.CaptureFixture[str]) -> None:
    root = logging.getLogger()
    saved = root.handlers[:], root.level
    try:
        configure_logging("INFO", ["s3cret-value", "https://u:pw-1@host.example"])
        logging.getLogger("httpx").info("HTTP Request: POST https://u:pw-1@host.example/ s3cret-value")
    finally:
        root.handlers[:], level = saved
        root.setLevel(level)

    err = capsys.readouterr().err
    assert "s3cret-value" not in err
    assert "pw-1" not in err
    assert "HTTP Request: POST" in err


# --- Error-body helpers ---


@pytest.mark.parametrize(
    ("text", "units", "expected"),
    [
        ("abcdef", 3, "abc"),
        ("abc", 5, "abc"),
        ("a\U0001f600b", 2, "a"),  # the split pair is dropped whole (ADR-0005)
        ("a\U0001f600b", 3, "a\U0001f600"),
        ("", 0, ""),
    ],
)
def test_head_is_a_utf16_slice(text: str, units: int, expected: str) -> None:
    assert head(text, units) == expected


@pytest.mark.parametrize(
    ("value", "text"),
    [
        ({"a": [1, 2.5, None, True], "b": {}, "c": []}, '{"a":[1,2.5,null,true],"b":{},"c":[]}'),
        ({"b": 1, "2": 2, "1": 3}, '{"1":3,"2":2,"b":1}'),
        ("x\n" + chr(0x2028), '"x\\n' + chr(0x2028) + '"'),  # JSON.stringify leaves U+2028 raw
        (float("nan"), "null"),
    ],
)
def test_stringify_compact_matches_json_stringify(value: object, text: str) -> None:
    assert stringify_compact(value) == text


PHASES = ("connect", "read", "write", "pool")


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("timeout", "sent"),
    [
        (None, dict.fromkeys(("connect", "read", "write", "pool"), 30.0)),
        (2.5, dict.fromkeys(("connect", "read", "write", "pool"), 2.5)),
    ],
    ids=["stdio-default", "bounded"],
)
async def test_typesafe_request_timeout(timeout: float | None, sent: dict[str, float | None]) -> None:
    """`evaluate` hands each attempt its deadline (ADR-0057): the policy's 30 s when the caller set
    none (stdio), else the caller's remaining budget. The SDK would read a bare `None` as its 10 s
    default, so a real number always reaches the transport."""
    seen: list[object] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request.extensions["timeout"])
        return httpx2.Response(200, json={"answers": {}})

    provider = typesafe(handler, NO_RETRIES)
    try:
        await provider.evaluate("state", QUESTIONS, "m", timeout)
    finally:
        await provider.aclose()

    # The cap is the caller's remaining budget, so the clock read leaves a sub-millisecond remainder.
    assert seen == [pytest.approx(sent, rel=1e-3)]


@pytest.mark.anyio
async def test_compatible_refuses_a_base_url_with_credentials() -> None:
    """Node `fetch` refuses it; httpx would silently swap the Bearer key for Basic auth."""
    url = "https://user:pw-9@jev.example/v1"
    provider = CompatibleProvider(Redactor(["compatible-test-key", url]), api_key="compatible-test-key", base_url=url)
    with respx.mock(assert_all_mocked=True, assert_all_called=False) as router:
        route = router.post("https://jev.example/v1").mock(return_value=httpx.Response(200, json={"answers": {}}))
        with pytest.raises(ProviderError) as caught:
            await provider.evaluate("state", QUESTIONS, "m", 5)
        await provider.aclose()

    assert str(caught.value) == (
        "Jev-compatible endpoint request failed: Request cannot be constructed from a URL that includes credentials"
    )
    assert not route.called


@pytest.mark.anyio
async def test_typesafe_refuses_a_base_url_with_credentials() -> None:
    sent: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        sent.append(request)
        return httpx2.Response(200, json={"answers": {}})

    provider = TypeSafeProvider(
        Redactor([]),
        api_key="typesafe-test-key",
        base_url="https://u:p@ts.example",
        transport=httpx2.MockTransport(handler),
    )
    with pytest.raises(ProviderError, match=r"^TypeSafe API request failed: Request cannot be constructed"):
        await provider.evaluate("state", QUESTIONS, "m", 5)
    await provider.aclose()

    assert sent == []


@pytest.mark.anyio
async def test_typesafe_refuses_a_redirect_off_the_configured_origin() -> None:
    """ADR-0023 for the SDK transport too: the origin gate rides the wrapper the SDK is given."""
    provider = TypeSafeProvider(Redactor([]), api_key="typesafe-test-key", base_url="https://ts.example")
    gate = provider._reject_cross_origin  # pyright: ignore[reportPrivateUsage]
    await gate(httpx2.Request("POST", "https://ts.example/v1/system-one"))
    with pytest.raises(ProviderError, match="a redirect left the configured origin and was blocked"):
        await gate(httpx2.Request("POST", "https://evil.example/v1/system-one"))
    await provider.aclose()


@pytest.mark.anyio
async def test_typesafe_gates_the_client_the_sdk_is_given() -> None:
    provider = TypeSafeProvider(Redactor([]), api_key="typesafe-test-key", base_url="https://ts.example")
    try:
        provider._sdk_client()  # pyright: ignore[reportPrivateUsage]
        wrapper = provider._wrapper  # pyright: ignore[reportPrivateUsage]
        assert wrapper is not None
        assert wrapper.event_hooks["request"] == [provider._reject_cross_origin]  # pyright: ignore[reportPrivateUsage]
        assert wrapper.timeout.connect is None and wrapper.timeout.read is None
    finally:
        await provider.aclose()
