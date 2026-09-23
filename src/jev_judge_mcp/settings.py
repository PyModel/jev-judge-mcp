"""Process configuration, read once from the environment at startup (ADR-0008).

The environment is the only source: no `.env` file, no secrets dir, no constructor
overrides, no CLI flags. Provider variables keep the reference's names exactly, so
each field carries its own alias and there is no prefix. Values are kept raw;
provider resolution and the model default belong to the resolver (P4).
"""

from pathlib import Path
from typing import Literal, cast, get_args

from pydantic import Field, SecretStr
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
    http_port: int = Field(default=8000, ge=1, le=65535, validation_alias="JEV_MCP_HTTP_PORT")
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

    def secret_values(self) -> list[str]:
        """Every configured secret value, for redaction — derived from the schema (ADR-0008, ADR-0017).

        Every `SecretStr` field participates, so a new credential cannot be forgotten; the naming
        guard in `tests/unit/test_settings.py` fails if a credential-named field is not `SecretStr`.
        """
        values: list[str] = []
        for name, field in type(self).model_fields.items():
            annotation = field.annotation
            if annotation is not SecretStr and SecretStr not in get_args(annotation):
                continue
            secret = cast("SecretStr | None", getattr(self, name))
            if secret is not None:
                values.append(secret.get_secret_value())
        return values


def load_settings() -> Settings:
    return Settings()
