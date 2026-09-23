import os
import re
from pathlib import Path

import pytest
from pydantic import SecretStr

from jev_judge_mcp.errors import Redactor
from jev_judge_mcp.settings import Settings, load_settings
from tests.support.secrets import is_secret_field, secret_env
from tests.support.stdio import server_env

SECRETS = {
    "TYPESAFE_API_KEY": "ts-secret",
    "TYPESAFE_BASE_URL": "https://user:pw@typesafe.example",
    "OPENROUTER_API_KEY": "sk-or-secret",
    "JEV_CLOUDFLARE_API_TOKEN": "jev-cf-secret",
    "CLOUDFLARE_API_TOKEN": "cf-secret",
    "AI_GATEWAY_API_KEY": "gw-secret",
    "JEV_API_KEY": "jev-secret",
    "JEV_API_BASE_URL": "https://user:pw@jev.example",
}


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    kept = server_env()
    for key in list(os.environ):
        if key not in kept:
            monkeypatch.delenv(key)


def test_defaults() -> None:
    settings = load_settings()
    assert settings.jev_provider == "auto"
    assert settings.jev_judge_mcp_model is None
    assert settings.transport == "stdio"
    assert settings.http_host == "127.0.0.1"
    assert settings.telemetry_payloads is False
    assert settings.secret_values() == []


def test_reads_reference_env_names(monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in SECRETS.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("JEV_PROVIDER", "Compatible")
    monkeypatch.setenv("JEV_MCP_MODEL", "jev-1.13")
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "acct")
    settings = load_settings()
    assert settings.jev_provider == "Compatible"
    assert settings.jev_judge_mcp_model == "jev-1.13"
    assert settings.cloudflare_account_id == "acct"
    assert sorted(settings.secret_values()) == sorted(SECRETS.values())


def test_repr_hides_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in SECRETS.items():
        monkeypatch.setenv(key, value)
    text = repr(load_settings()) + str(load_settings())
    for value in SECRETS.values():
        assert value not in text


def test_env_is_the_only_source(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("JEV_API_KEY=from-dotenv\n")
    monkeypatch.chdir(tmp_path)
    assert Settings(jev_api_key=SecretStr("from-init")).jev_api_key is None
    assert load_settings().jev_api_key is None


def test_names_are_case_sensitive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("jev_api_key", "lowercase")
    assert load_settings().jev_api_key is None


def test_http_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JEV_MCP_TRANSPORT", "streamable-http")
    monkeypatch.setenv("JEV_MCP_HTTP_PORT", "9123")
    settings = load_settings()
    assert settings.transport == "streamable-http"
    assert settings.http_port == 9123


def test_telemetry_payloads_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JEV_MCP_TELEMETRY_PAYLOADS", "1")
    assert load_settings().telemetry_payloads is True


def test_rejects_unknown_transport(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JEV_MCP_TRANSPORT", "sse")
    with pytest.raises(ValueError, match="JEV_MCP_TRANSPORT"):
        load_settings()


CREDENTIAL_SUFFIXES = re.compile(r"(_key|_token|_secret|_password|_base_url)$")


def test_credential_named_fields_are_secret_str() -> None:
    """ADR-0017 inverse invariant: a credential added as plain `str` cannot slip past the derivation."""
    for name, field in Settings.model_fields.items():
        if CREDENTIAL_SUFFIXES.search(name):
            assert is_secret_field(field), f"{name} is credential-named and must be SecretStr"


def test_every_secret_field_participates_in_redaction(monkeypatch: pytest.MonkeyPatch) -> None:
    """ADR-0017: env populated from the schema, redaction through the production derivation.

    This test derives its marker set from `Settings.model_fields` — not from `secret_values()` — so
    a field the production derivation skips (or a new SecretStr field) changes what is checked here.
    """
    env = secret_env()
    assert env, "no secret fields found — the derivation has nothing to protect"
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    redact = Redactor(load_settings().secret_values())
    hostile = " ".join(f"Bearer {value} {value}" for value in env.values())
    message = redact(hostile)
    for value in env.values():
        assert value not in message


def test_redactor_covers_the_normalized_form_of_a_url_secret() -> None:
    """An HTTP client logs a URL lowercased, path added, default port dropped (ADR-0008)."""
    redact = Redactor(["https://TS.Example/v1?ticket=xyz", "https://Api.Example:443/p"])
    logged = "GET https://ts.example/v1?ticket=xyz failed; retrying https://api.example/p"
    assert redact(logged) == "GET [redacted] failed; retrying [redacted]"
    # The exact configured form is still covered, and a non-URL value adds no variants.
    assert redact("https://TS.Example/v1?ticket=xyz") == "[redacted]"
    assert Redactor(["plain-token"])("plain-token") == "[redacted]"
