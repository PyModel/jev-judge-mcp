"""Provider behavior outside the shared contract: slugs, Cloudflare's v4 envelope, redaction."""

import io
import json
import logging
from typing import Any

import httpcore
import httpx
import pytest
import respx

from jev_judge_mcp.domain import NoulCriteria, NoulQuestion, Usage
from jev_judge_mcp.providers import NO_RETRIES, Evaluation, ProviderError
from jev_judge_mcp.providers.base import decode_body, parse_envelope
from jev_judge_mcp.providers.cloudflare import CloudflareProvider, cloudflare_slug
from jev_judge_mcp.providers.compatible import CompatibleProvider
from jev_judge_mcp.providers.openrouter import openrouter_slug
from jev_judge_mcp.redaction.values import REDACTED, RedactingFilter, Redactor, configure_logging
from jev_judge_mcp.serialize import stringify_compact
from jev_judge_mcp.text import head
from tests.support.retries import fast_retries as fast_retries

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


@pytest.mark.parametrize("record_kind", ["valid-format", "malformed-format", "cached-traceback", "stack-info"])
def test_redacting_filter_covers_message_args_and_traceback(record_kind: str) -> None:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)

    def keep_formatted_traceback(record: logging.LogRecord) -> bool:
        if record.exc_info is not None:
            logging.Formatter().format(record)
            record.exc_info = None
        return True

    if record_kind == "cached-traceback":
        handler.addFilter(keep_formatted_traceback)
    handler.addFilter(RedactingFilter(Redactor(["s3cret-value"])))
    logger = logging.Logger("tests.redaction")
    logger.addHandler(handler)
    logger.propagate = False
    try:
        logger.warning("key %s in %r", "s3cret-value", {"k": "s3cret-value"})
        if record_kind == "malformed-format":
            logger.warning("key %d", "s3cret-value")
        try:
            raise RuntimeError("boom s3cret-value")
        except RuntimeError:
            logger.exception("failed with s3cret-value")
        if record_kind == "stack-info":
            logger.error("stack evidence %s", "s3cret-value", stack_info=True)
    finally:
        logger.removeHandler(handler)

    output = stream.getvalue()
    assert "s3cret-value" not in output
    assert "key [redacted] in {'k': '[redacted]'}" in output
    assert "RuntimeError: boom [redacted]" in output
    if record_kind == "malformed-format":
        assert "key %d" in output and "('[redacted]',)" in output
    if record_kind == "stack-info":
        _, _, stack = output.partition("Stack (most recent call last):")
        assert "[redacted]" in stack


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


@pytest.mark.anyio
async def test_debug_logging_hides_http_wire_payloads_by_default(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    root = logging.getLogger()
    saved = root.handlers[:], root.level
    backend = httpcore.AsyncMockBackend([b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n"])
    monkeypatch.setattr(httpcore.AnyIOBackend, "connect_tcp", backend.connect_tcp)
    try:
        configure_logging("DEBUG")
        async with httpx.AsyncClient(trust_env=False) as client:
            response = await client.post("http://offline.example", content=b"private file contents", timeout=1)
        assert response.status_code == 200
        logging.getLogger("jev_judge_mcp").debug("application diagnostic")
    finally:
        root.handlers[:], root.level = saved

    err = capsys.readouterr().err
    assert "send_request_body.started" in err
    assert "HTTP Request: POST" in err
    assert "application diagnostic" in err
    assert "private file contents" not in err


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


@pytest.mark.anyio
@pytest.mark.parametrize("status", [200, 500])
@pytest.mark.parametrize("buffered", [False, True], ids=["streaming", "buffered"])
async def test_scout_019_stream_limit_stops_consumption(
    status: int, buffered: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    from jev_judge_mcp.providers import base

    monkeypatch.setattr(base, "SUCCESS_BYTES_MAX", 8)
    monkeypatch.setattr(base, "ERROR_BYTES_MAX", 8)
    consumed: list[bytes] = []
    closed: list[bool] = []

    class Body(httpx.AsyncByteStream):
        async def __aiter__(self):
            for chunk in [b"1234", b"56789", b"unused"]:
                consumed.append(chunk)
                yield chunk

        async def aclose(self):
            closed.append(True)

    body = b"123456789" + b"x" * 200 + b"omitted-tail"
    response = httpx.Response(status, content=body) if buffered else httpx.Response(status, stream=Body())
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda _: response))
    provider = CompatibleProvider(Redactor([]), api_key="", base_url="https://fixture.invalid", client=client)
    try:
        with pytest.raises(ProviderError) as caught:
            await provider.evaluate({}, QUESTIONS, "fixture", 1)
    finally:
        await provider.aclose()
    if status == 200:
        assert "byte limit" in str(caught.value)
    else:
        assert caught.value.status == status
        assert "12345678" in str(caught.value)
        if buffered:
            assert "123456789" in str(caught.value)
            assert "omitted-tail" not in str(caught.value)
        else:
            assert "123456789" not in str(caught.value)
    if buffered:
        assert response.content == body
        assert consumed == closed == []
    else:
        assert consumed == [b"1234", b"56789"]
        assert closed == [True]


@pytest.mark.anyio
@pytest.mark.parametrize("failure", [httpx.ReadTimeout("read"), httpx.ReadError("closed"), 501, 505])
async def test_scout_005_ambiguous_post_is_never_replayed(
    failure: httpx.RequestError | int, fast_retries: list[float]
) -> None:
    attempts: list[httpx.Request] = []

    def send(request: httpx.Request) -> httpx.Response:
        attempts.append(request)
        if isinstance(failure, int):
            return httpx.Response(failure)
        raise failure

    client = httpx.AsyncClient(transport=httpx.MockTransport(send))
    provider = CompatibleProvider(Redactor([]), api_key="", base_url="https://fixture.invalid", client=client)
    try:
        with pytest.raises(ProviderError, match="Completion is uncertain"):
            await provider.evaluate({}, QUESTIONS, "fixture", 10)
    finally:
        await provider.aclose()
    assert len(attempts) == 1


@pytest.mark.anyio
async def test_scout_019_unrequested_encoding_closes_before_consumption() -> None:
    consumed: list[bool] = []
    closed: list[bool] = []

    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            consumed.append(True)
            yield b""

        async def aclose(self) -> None:
            closed.append(True)

    def send(request: httpx.Request) -> httpx.Response:
        assert request.headers["accept-encoding"] == "identity"
        return httpx.Response(200, headers={"content-encoding": "gzip"}, stream=Stream())

    client = httpx.AsyncClient(transport=httpx.MockTransport(send))
    provider = CompatibleProvider(Redactor([]), api_key="", base_url="https://fixture.invalid", client=client)
    try:
        with pytest.raises(ProviderError, match="unsupported content encoding"):
            await provider.evaluate({}, QUESTIONS, "fixture", 1)
    finally:
        await provider.aclose()
    assert not consumed and closed == [True]
