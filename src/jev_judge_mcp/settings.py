"""Process configuration, read once from the environment at startup (ADR-0008).

The environment is the only source: no `.env` file, no secrets dir, no constructor
overrides, no CLI flags. Provider variables keep the reference's names exactly, so
each field carries its own alias and there is no prefix. Values are kept raw;
provider resolution and the model default belong to the resolver (P4).
"""

from pathlib import Path
from typing import Literal, cast, get_args

from pydantic import Field, SecretStr
from pydantic.aliases import AliasChoices
from pydantic.fields import FieldInfo
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict

Transport = Literal["stdio", "streamable-http"]
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(case_sensitive=True, extra="ignore", frozen=True)

    # Provider selection and model (reference names; resolved in P4).
    jev_provider: str = Field(default="auto", validation_alias="JEV_PROVIDER")
    jev_judge_mcp_model: str | None = Field(default=None, validation_alias="JEV_MCP_MODEL")

    # Credentials and endpoints. Base URLs are secrets too: they can carry userinfo (ADR-0008).
    typesafe_api_key: SecretStr | None = Field(default=None, validation_alias="TYPESAFE_API_KEY")
    typesafe_base_url: SecretStr | None = Field(default=None, validation_alias="TYPESAFE_BASE_URL")
    openrouter_api_key: SecretStr | None = Field(default=None, validation_alias="OPENROUTER_API_KEY")
    jev_cloudflare_api_token: SecretStr | None = Field(default=None, validation_alias="JEV_CLOUDFLARE_API_TOKEN")
    cloudflare_api_token: SecretStr | None = Field(default=None, validation_alias="CLOUDFLARE_API_TOKEN")
    cloudflare_account_id: str | None = Field(default=None, validation_alias="CLOUDFLARE_ACCOUNT_ID")
    ai_gateway_api_key: SecretStr | None = Field(default=None, validation_alias="AI_GATEWAY_API_KEY")
    jev_api_key: SecretStr | None = Field(default=None, validation_alias="JEV_API_KEY")
    jev_api_base_url: SecretStr | None = Field(default=None, validation_alias="JEV_API_BASE_URL")

    # Optional provider response cache (ADR-0047): off unless JEV_MCP_CACHE is truthy.
    jev_judge_mcp_cache: bool = Field(default=False, validation_alias="JEV_MCP_CACHE")
    cache_dir: Path | None = Field(default=None, validation_alias="JEV_MCP_CACHE_DIR")
    # The key file `jev-judge-mcp setup` writes (ADR-0046); env wins over the file at resolution.
    key_file: Path | None = Field(default=None, validation_alias="JEV_MCP_KEY_FILE")

    # Python-only process settings; the reference is stdio-only.
    transport: Transport = Field(default="stdio", validation_alias="JEV_MCP_TRANSPORT")
    http_host: str = Field(default="127.0.0.1", validation_alias="JEV_MCP_HTTP_HOST")
    http_port: int = Field(default=8088, ge=1, le=65535, validation_alias="JEV_MCP_HTTP_PORT")
    # The bearer token every HTTP request must carry (ADR-0050). `SecretStr` joins redaction per ADR-0017.
    http_token: SecretStr | None = Field(default=None, validation_alias="JEV_MCP_HTTP_TOKEN")
    log_level: LogLevel = Field(default="INFO", validation_alias="JEV_MCP_LOG_LEVEL")
    # Debug only: let telemetry spans record payload text (arguments, results, patterns). Off by default.
    telemetry_payloads: bool = Field(default=False, validation_alias="JEV_MCP_TELEMETRY_PAYLOADS")

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (env_settings,)

    def named_secrets(self) -> list[tuple[str, str]]:
        """(environment variable, value) for every configured, non-empty secret (ADR-0008, ADR-0017).

        `secret_values()` feeds the `Redactor`; this pairs each value with its variable name so a
        startup gate can reject an unsafe one by name (ADR-0050). Empty values stay out: every
        reader treats an empty variable as unset, and the `Redactor` skips them anyway.
        """
        named: list[tuple[str, str]] = []
        for name, field in type(self).model_fields.items():
            annotation = field.annotation
            if annotation is not SecretStr and SecretStr not in get_args(annotation):
                continue
            secret = cast("SecretStr | None", getattr(self, name))
            if secret is None:
                continue
            value = secret.get_secret_value()
            if value:
                named.append((_env_var_name(name, field), value))
        return named

    def secret_values(self) -> list[str]:
        """Every configured secret value, for redaction — derived from the schema (ADR-0008, ADR-0017).

        Every `SecretStr` field participates, so a new credential cannot be forgotten; the naming
        guard in `tests/unit/test_settings.py` fails if a credential-named field is not `SecretStr`.
        """
        return [value for _, value in self.named_secrets()]


def _env_var_name(name: str, field: FieldInfo) -> str:
    """The variable name a field reads: its validation alias, else the uppercased field name."""
    alias = field.validation_alias
    if isinstance(alias, str):
        return alias
    if isinstance(alias, AliasChoices):
        for choice in alias.choices:
            if isinstance(choice, str):
                return choice
    return name.upper()


def load_settings() -> Settings:
    return Settings()
