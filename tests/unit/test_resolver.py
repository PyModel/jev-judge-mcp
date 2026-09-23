"""Provider resolution beyond the recorded error texts (`provider.ts:35-77`, ADR-0007)."""

import os
import re
from pathlib import Path

import pytest

from jev_judge_mcp.providers import ProviderConfigError, resolve_model, resolve_provider
from jev_judge_mcp.providers.cloudflare import CloudflareProvider
from jev_judge_mcp.providers.compatible import CompatibleProvider
from jev_judge_mcp.providers.openrouter import OpenRouterProvider
from jev_judge_mcp.providers.resolver import NO_CREDENTIALS, VERCEL_UNSUPPORTED
from jev_judge_mcp.providers.typesafe import TypeSafeProvider
from jev_judge_mcp.settings import load_settings
from tests.support.stdio import server_env

TYPESAFE = {"TYPESAFE_API_KEY": "ts"}
OPENROUTER = {"OPENROUTER_API_KEY": "sk-or-v1"}
CLOUDFLARE = {"CLOUDFLARE_API_TOKEN": "cf", "CLOUDFLARE_ACCOUNT_ID": "acct"}
VERCEL = {"AI_GATEWAY_API_KEY": "gw"}
COMPATIBLE = {"JEV_API_KEY": "jev", "JEV_API_BASE_URL": "https://jev.example/v1"}


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    kept = server_env()
    for key in list(os.environ):
        if key not in kept:
            monkeypatch.delenv(key)


def resolve(monkeypatch: pytest.MonkeyPatch, *envs: dict[str, str]) -> object:
    for env in envs:
        for key, value in env.items():
            monkeypatch.setenv(key, value)
    return resolve_provider(load_settings())


@pytest.mark.parametrize(
    ("envs", "expected"),
    [
        ((TYPESAFE, OPENROUTER, CLOUDFLARE, VERCEL, COMPATIBLE), TypeSafeProvider),
        ((OPENROUTER, CLOUDFLARE, VERCEL, COMPATIBLE), OpenRouterProvider),
        ((CLOUDFLARE, VERCEL, COMPATIBLE), CloudflareProvider),
        ((COMPATIBLE,), CompatibleProvider),
    ],
    ids=["typesafe", "openrouter", "cloudflare", "compatible"],
)
def test_auto_order(monkeypatch: pytest.MonkeyPatch, envs: tuple[dict[str, str], ...], expected: type) -> None:
    assert isinstance(resolve(monkeypatch, *envs), expected)


def test_auto_reaching_the_vercel_slot_is_unsupported(monkeypatch: pytest.MonkeyPatch) -> None:
    """ADR-0007: the slot is kept, so compatible behind it is not silently chosen instead."""
    with pytest.raises(ProviderConfigError, match=f"^{re.escape(VERCEL_UNSUPPORTED)}$"):
        resolve(monkeypatch, VERCEL, COMPATIBLE)


@pytest.mark.parametrize("name", ["vercel", "Vercel"])
def test_explicit_vercel_is_unsupported_even_with_its_key(monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    with pytest.raises(ProviderConfigError, match=f"^{re.escape(VERCEL_UNSUPPORTED)}$"):
        resolve(monkeypatch, {"JEV_PROVIDER": name}, VERCEL, COMPATIBLE)


@pytest.mark.parametrize(
    ("name", "env", "expected"),
    [
        ("compatible", COMPATIBLE, CompatibleProvider),
        ("CLOUDFLARE", CLOUDFLARE, CloudflareProvider),
        ("OpenRouter", OPENROUTER, OpenRouterProvider),
        ("typesafe", TYPESAFE, TypeSafeProvider),
    ],
)
def test_explicit_name_beats_auto_order(
    monkeypatch: pytest.MonkeyPatch, name: str, env: dict[str, str], expected: type
) -> None:
    everything = (TYPESAFE, OPENROUTER, CLOUDFLARE, COMPATIBLE)
    assert isinstance(resolve(monkeypatch, *everything, {"JEV_PROVIDER": name}), expected)


@pytest.mark.parametrize("value", ["", "auto", "AUTO", "bogus"])
def test_non_provider_names_fall_through_to_auto(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    assert isinstance(resolve(monkeypatch, {"JEV_PROVIDER": value}, COMPATIBLE), CompatibleProvider)


@pytest.mark.parametrize(
    "env",
    [
        {"TYPESAFE_API_KEY": ""},
        {"OPENROUTER_API_KEY": ""},
        {"OPENROUTER_API_KEY": "SK-OR-upper"},
        {"CLOUDFLARE_API_TOKEN": "cf", "CLOUDFLARE_ACCOUNT_ID": ""},
        {"CLOUDFLARE_API_TOKEN": "", "JEV_CLOUDFLARE_API_TOKEN": "", "CLOUDFLARE_ACCOUNT_ID": "acct"},
        {"AI_GATEWAY_API_KEY": ""},
        {"JEV_API_KEY": "", "JEV_API_BASE_URL": "https://jev.example"},
    ],
    ids=str,
)
def test_empty_values_are_unset_as_in_js(monkeypatch: pytest.MonkeyPatch, env: dict[str, str]) -> None:
    with pytest.raises(ProviderConfigError, match=r"^No Jev provider credentials found"):
        resolve(monkeypatch, env)
    assert NO_CREDENTIALS.endswith("set JEV_PROVIDER to choose explicitly.")


def test_explicit_typesafe_with_empty_key_is_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ProviderConfigError, match=r"^JEV_PROVIDER=typesafe but TYPESAFE_API_KEY is not set\.$"):
        resolve(monkeypatch, {"JEV_PROVIDER": "typesafe", "TYPESAFE_API_KEY": ""})


def test_explicit_compatible_with_empty_url_names_it(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ProviderConfigError, match=r"^JEV_PROVIDER=compatible but JEV_API_BASE_URL is not set\. "):
        resolve(monkeypatch, {"JEV_PROVIDER": "compatible", "JEV_API_KEY": "k", "JEV_API_BASE_URL": ""})


@pytest.mark.parametrize(
    ("tokens", "used"),
    [
        ({"JEV_CLOUDFLARE_API_TOKEN": "jev-cf", "CLOUDFLARE_API_TOKEN": "cf"}, "jev-cf"),
        ({"JEV_CLOUDFLARE_API_TOKEN": "", "CLOUDFLARE_API_TOKEN": "cf"}, "cf"),
        ({"CLOUDFLARE_API_TOKEN": "cf"}, "cf"),
    ],
)
def test_cloudflare_token_precedence(monkeypatch: pytest.MonkeyPatch, tokens: dict[str, str], used: str) -> None:
    provider = resolve(monkeypatch, tokens, {"CLOUDFLARE_ACCOUNT_ID": "acct"})
    assert isinstance(provider, CloudflareProvider)
    assert provider._api_token == used  # pyright: ignore[reportPrivateUsage]


def test_typesafe_gets_the_configured_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = resolve(monkeypatch, TYPESAFE, {"TYPESAFE_BASE_URL": "https://ts.example"})
    assert isinstance(provider, TypeSafeProvider)
    assert provider._base_url == "https://ts.example"  # pyright: ignore[reportPrivateUsage]


def test_resolved_providers_redact_every_configured_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = resolve(monkeypatch, TYPESAFE, OPENROUTER, CLOUDFLARE, VERCEL, COMPATIBLE)
    assert isinstance(provider, TypeSafeProvider)
    text = " ".join(["ts", "sk-or-v1", "cf", "gw", "jev", "https://jev.example/v1"])
    assert provider._redact(text) == " ".join(["[redacted]"] * 6)  # pyright: ignore[reportPrivateUsage]


@pytest.mark.parametrize(("env", "model"), [({}, "jev-latest"), ({"JEV_MCP_MODEL": "jev-1.12"}, "jev-1.12")])
def test_model_default(monkeypatch: pytest.MonkeyPatch, env: dict[str, str], model: str) -> None:
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    assert resolve_model(load_settings()) == model


def test_empty_model_stays_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    """`JEV_MCP_MODEL ?? "jev-latest"` (`index.ts:72`): only an unset variable takes the default."""
    monkeypatch.setenv("JEV_MCP_MODEL", "")
    assert resolve_model(load_settings()) == ""


# --- key-file fallback (ADR-0046) ---


def _stored_key_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, value: str) -> Path:
    path = tmp_path / "config" / "key"
    path.parent.mkdir(parents=True)
    path.write_text(value + "\n", encoding="utf-8")
    monkeypatch.setenv("JEV_MCP_KEY_FILE", str(path))
    return path


def test_a_stored_key_resolves_typesafe(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _stored_key_file(monkeypatch, tmp_path, "sk-file-key")
    assert isinstance(resolve(monkeypatch), TypeSafeProvider)


def test_the_environment_variable_wins_over_the_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _stored_key_file(monkeypatch, tmp_path, "sk-file-key")
    provider = resolve(monkeypatch, TYPESAFE)
    assert isinstance(provider, TypeSafeProvider)


def test_explicit_typesafe_resolves_from_the_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _stored_key_file(monkeypatch, tmp_path, "sk-file-key")
    monkeypatch.setenv("JEV_PROVIDER", "typesafe")
    assert isinstance(resolve(monkeypatch), TypeSafeProvider)


def test_the_stored_key_is_redacted_on_the_error_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The stored value joins the redaction set (ADR-0017): a URL echo cannot leak it."""
    _stored_key_file(monkeypatch, tmp_path, "sk-file-secret")
    provider = resolve(monkeypatch)
    assert isinstance(provider, TypeSafeProvider)
    error = provider._redact(  # pyright: ignore[reportPrivateUsage] -- the redaction set IS the seam under test
        "request to https://api.example with bearer sk-file-secret failed"
    )
    assert "sk-file-secret" not in error
    assert "bearer [redacted]" in error


def test_an_empty_file_still_reports_no_credentials(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    path = _stored_key_file(monkeypatch, tmp_path, "   ")
    monkeypatch.setenv("JEV_PROVIDER", "typesafe")
    with pytest.raises(ProviderConfigError, match="TYPESAFE_API_KEY is not set"):
        resolve(monkeypatch)
    path.unlink()
    monkeypatch.delenv("JEV_PROVIDER")
    with pytest.raises(ProviderConfigError, match=re.escape(NO_CREDENTIALS)):
        resolve(monkeypatch)
