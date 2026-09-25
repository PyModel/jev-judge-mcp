"""HTTP access control: the startup gates and the bearer-token middleware (ADR-0050).

The startup decision is unit-tested directly; the middleware runs in process over the real SDK
app built for `127.0.0.1`, so nothing here binds anything but loopback — and the refusal cases
never bind at all.
"""

import json
import os
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr
from starlette.applications import Starlette
from starlette.types import ASGIApp

from jev_judge_mcp.http_auth import (
    HTTP_TOKEN_MIN_LENGTH,
    PROTECTED_HOSTS,
    BearerTokenMiddleware,
    ensure_http_access_control,
)
from jev_judge_mcp.server import build_server, ensure_secrets_redactable, http_asgi_app
from jev_judge_mcp.settings import Settings, load_settings
from tests.support.stdio import INITIALIZE, server_env

TOKEN = "test-token-" + "x" * 32

# Exactly the literals the SDK protects with Host/Origin validation — and only these.
PROTECTED = ["localhost", "127.0.0.1", "::1"]
# Every other host needs the token, including other 127.0.0.0/8 addresses and other ::1 spellings.
TOKEN_REQUIRED = [
    "127.9.9.9",
    "0:0:0:0:0:0:0:1",
    "0.0.0.0",
    "::",
    "192.0.2.1",
    "jev.example",
    "2001:db8::1",
]


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    kept = server_env()
    for key in list(os.environ):
        if key not in kept:
            monkeypatch.delenv(key)


def settings_with(monkeypatch: pytest.MonkeyPatch, **env: str) -> Settings:
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return load_settings()


def http_settings(monkeypatch: pytest.MonkeyPatch, *, token: str | None) -> Settings:
    monkeypatch.setenv("JEV_MCP_TRANSPORT", "streamable-http")
    if token is None:
        monkeypatch.delenv("JEV_MCP_HTTP_TOKEN", raising=False)
    else:
        monkeypatch.setenv("JEV_MCP_HTTP_TOKEN", token)
    return load_settings()


def token_app(monkeypatch: pytest.MonkeyPatch, token: str) -> tuple[BearerTokenMiddleware, Starlette]:
    """The middleware over the real SDK app, wired as `_serve` does, with the inner app for its lifespan."""
    settings = http_settings(monkeypatch, token=token)
    inner = build_server(settings).streamable_http_app(host=settings.http_host)
    return BearerTokenMiddleware(inner, SecretStr(token)), inner


def bare_app(monkeypatch: pytest.MonkeyPatch) -> Starlette:
    settings = http_settings(monkeypatch, token=None)
    return build_server(settings).streamable_http_app(host=settings.http_host)


async def post(app: ASGIApp, payload: object, headers: dict[str, str] | None = None) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
        return await client.post(
            "/mcp",
            json=payload,  # type: ignore[arg-type]
            headers={"Accept": "application/json, text/event-stream", "Host": "127.0.0.1:8000", **(headers or {})},
        )


# --- the startup decision ---


@pytest.mark.parametrize("host", PROTECTED)
def test_sdk_protected_hosts_bind_without_a_token(monkeypatch: pytest.MonkeyPatch, host: str) -> None:
    settings = settings_with(monkeypatch, JEV_MCP_TRANSPORT="streamable-http", JEV_MCP_HTTP_HOST=host)
    ensure_http_access_control(settings)


@pytest.mark.parametrize("host", TOKEN_REQUIRED)
def test_unprotected_hosts_without_a_token_refuse_to_start(monkeypatch: pytest.MonkeyPatch, host: str) -> None:
    settings = settings_with(monkeypatch, JEV_MCP_TRANSPORT="streamable-http", JEV_MCP_HTTP_HOST=host)
    with pytest.raises(SystemExit, match="JEV_MCP_HTTP_TOKEN"):
        ensure_http_access_control(settings)


def test_a_long_token_admits_a_non_loopback_bind(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = settings_with(
        monkeypatch,
        JEV_MCP_TRANSPORT="streamable-http",
        JEV_MCP_HTTP_HOST="192.0.2.1",
        JEV_MCP_HTTP_TOKEN=TOKEN,
    )
    assert len(TOKEN) >= HTTP_TOKEN_MIN_LENGTH
    ensure_http_access_control(settings)


def test_stdio_never_enters_the_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = settings_with(monkeypatch, JEV_MCP_TRANSPORT="stdio", JEV_MCP_HTTP_HOST="192.0.2.1")
    ensure_http_access_control(settings)


def test_a_short_token_is_refused_on_any_host_and_never_echoed(monkeypatch: pytest.MonkeyPatch) -> None:
    short = "too-short-token"
    for host in ("127.0.0.1", "192.0.2.1"):
        settings = settings_with(
            monkeypatch,
            JEV_MCP_TRANSPORT="streamable-http",
            JEV_MCP_HTTP_HOST=host,
            JEV_MCP_HTTP_TOKEN=short,
        )
        with pytest.raises(SystemExit, match="JEV_MCP_HTTP_TOKEN") as exc:
            ensure_http_access_control(settings)
        assert short not in str(exc.value)


def test_the_token_generation_hint_is_in_the_refusal(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = settings_with(monkeypatch, JEV_MCP_TRANSPORT="streamable-http", JEV_MCP_HTTP_HOST="192.0.2.1")
    with pytest.raises(SystemExit, match=r"secrets\.token_urlsafe"):
        ensure_http_access_control(settings)


# --- the redaction floor (every configured secret) ---


def test_a_short_configured_secret_refuses_startup_naming_the_variable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = settings_with(monkeypatch, TYPESAFE_API_KEY="abc")
    with pytest.raises(SystemExit, match="TYPESAFE_API_KEY") as exc:
        ensure_secrets_redactable(settings)
    assert "abc" not in str(exc.value)


def test_an_http_token_under_the_redaction_floor_is_refused_too(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = settings_with(monkeypatch, JEV_MCP_HTTP_TOKEN="tok")
    with pytest.raises(SystemExit, match="JEV_MCP_HTTP_TOKEN") as exc:
        ensure_secrets_redactable(settings)
    assert "tok" not in str(exc.value)


def test_secrets_at_the_floor_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    """ADR-0046's floor is behavioral: seven characters is refused, eight is at the floor and passes."""
    below = settings_with(monkeypatch, TYPESAFE_API_KEY="e" * 7)
    with pytest.raises(SystemExit, match="TYPESAFE_API_KEY") as exc:
        ensure_secrets_redactable(below)
    assert "eeeeeee" not in str(exc.value)
    ensure_secrets_redactable(settings_with(monkeypatch, TYPESAFE_API_KEY="e" * 8))


def test_an_empty_secret_stays_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = settings_with(monkeypatch, TYPESAFE_API_KEY="")
    ensure_secrets_redactable(settings)


def test_a_short_key_file_key_refuses_startup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    key_file = tmp_path / "key"
    key_file.write_text("abc\n")
    settings = settings_with(monkeypatch, JEV_MCP_KEY_FILE=str(key_file))
    with pytest.raises(SystemExit, match="JEV_MCP_KEY_FILE") as exc:
        ensure_secrets_redactable(settings)
    assert "abc" not in str(exc.value)


# --- the middleware over the real app ---


@pytest.mark.anyio
async def test_missing_wrong_and_correct_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    app, inner = token_app(monkeypatch, TOKEN)
    async with inner.router.lifespan_context(inner):
        missing = await post(app, INITIALIZE)
        assert missing.status_code == 401
        assert missing.headers["www-authenticate"] == "Bearer"
        assert TOKEN not in missing.text

        wrong = await post(app, INITIALIZE, headers={"Authorization": f"Bearer {'w' * 48}"})
        assert wrong.status_code == 401
        assert wrong.headers["www-authenticate"] == "Bearer"
        assert TOKEN not in wrong.text

        correct = await post(app, INITIALIZE, headers={"Authorization": f"Bearer {TOKEN}"})
        assert correct.status_code == 200
        data = next(line[len("data: ") :] for line in correct.text.splitlines() if line.startswith("data: "))
        result = json.loads(data)["result"]
        assert result["serverInfo"]["name"] == "jev-mcp"


@pytest.mark.anyio
async def test_a_non_bearer_scheme_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    app, inner = token_app(monkeypatch, TOKEN)
    async with inner.router.lifespan_context(inner):
        basic = await post(app, INITIALIZE, headers={"Authorization": f"Basic {TOKEN}"})
        assert basic.status_code == 401


@pytest.mark.anyio
async def test_loopback_default_keeps_host_and_origin_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    """The loopback default without a token is unchanged: forged Host/Origin never reach the tools."""
    app = bare_app(monkeypatch)
    async with app.router.lifespan_context(app):
        forged_host = await post(app, INITIALIZE, headers={"Host": "evil.example"})
        assert forged_host.status_code == 421
        forged_origin = await post(app, INITIALIZE, headers={"Origin": "http://evil.example"})
        assert forged_origin.status_code == 403
        normal = await post(app, INITIALIZE)
        assert normal.status_code == 200


@pytest.mark.anyio
@pytest.mark.parametrize("host", PROTECTED_HOSTS)
async def test_each_exempt_host_is_actually_protected_by_the_sdk(monkeypatch: pytest.MonkeyPatch, host: str) -> None:
    """Pins the SDK's auto-enabled Host/Origin list: every host exempt from the token must answer a
    forged Host with 421, in process and without binding. If the SDK's list changes, the exemption
    must change with it (ADR-0050).
    """
    settings = settings_with(monkeypatch, JEV_MCP_TRANSPORT="streamable-http", JEV_MCP_HTTP_HOST=host)
    assert settings.http_token is None
    ensure_http_access_control(settings)
    app = http_asgi_app(build_server(settings), settings)
    assert isinstance(app, Starlette)
    async with app.router.lifespan_context(app):
        forged = await post(app, INITIALIZE, headers={"Host": "evil.example"})
        assert forged.status_code == 421


def test_http_asgi_app_wraps_only_when_a_token_is_set(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = http_settings(monkeypatch, token=None)
    assert isinstance(http_asgi_app(build_server(settings), settings), Starlette)
    settings = http_settings(monkeypatch, token=TOKEN)
    wrapped = http_asgi_app(build_server(settings), settings)
    assert isinstance(wrapped, BearerTokenMiddleware)
    assert isinstance(wrapped.app, Starlette)
