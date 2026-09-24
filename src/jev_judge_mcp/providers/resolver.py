"""Provider resolution from process configuration (`provider.ts:35-77`, ADR-0007, ADR-0008).

Explicit `JEV_PROVIDER` (lowercased) wins; an unknown name falls through to auto resolution in the
reference order typesafe, openrouter, cloudflare, vercel, compatible. A variable set to the empty
string counts as unset, as it is falsy in JS. Vercel keeps its slot but is unsupported (ADR-0007).
Resolution raises `ProviderConfigError` before any request, so the tools report it per call.
"""

from pydantic import SecretStr

from jev_judge_mcp import keyfile
from jev_judge_mcp.errors import Redactor
from jev_judge_mcp.providers.base import JevProvider, ProviderConfigError
from jev_judge_mcp.providers.cloudflare import CloudflareProvider
from jev_judge_mcp.providers.compatible import CompatibleProvider
from jev_judge_mcp.providers.openrouter import OpenRouterProvider
from jev_judge_mcp.providers.retry import RetryPolicy
from jev_judge_mcp.providers.typesafe import TypeSafeProvider
from jev_judge_mcp.settings import Settings

DEFAULT_MODEL = "jev-latest"

VERCEL_UNSUPPORTED = (
    "vercel provider is not supported by the Python server; use typesafe, openrouter, cloudflare or compatible"
)
NO_CREDENTIALS = (
    "No Jev provider credentials found. Set TYPESAFE_API_KEY, OPENROUTER_API_KEY (sk-or-), Cloudflare token + "
    "CLOUDFLARE_ACCOUNT_ID, AI_GATEWAY_API_KEY, or JEV_API_KEY + JEV_API_BASE_URL; set JEV_PROVIDER to choose "
    "explicitly."
)


def resolve_model(settings: Settings) -> str:
    """`process.env.JEV_MCP_MODEL ?? "jev-latest"` (`index.ts:72`): an empty model stays empty."""
    return DEFAULT_MODEL if settings.jev_judge_mcp_model is None else settings.jev_judge_mcp_model


def resolve_provider(settings: Settings, *, retry: RetryPolicy | None = None) -> JevProvider:
    """The provider `settings` select, or `ProviderConfigError` with the reference's text.

    A key stored by `jev-judge-mcp setup` stands in for `TYPESAFE_API_KEY` when the variable is
    unset (ADR-0046); the variable always wins. The stored value joins the redaction set for this
    resolver's providers, so it is covered exactly like a configured secret (ADR-0017). `retry`
    injects the provider retry policy (ADR-0057); `None` means the default.
    """
    stored = keyfile.stored_key(settings)
    redact = Redactor([*settings.secret_values(), stored] if stored else settings.secret_values())
    explicit = settings.jev_provider.lower()
    typesafe_key = _value(settings.typesafe_api_key) or stored
    openrouter_key = _value(settings.openrouter_api_key)
    has_openrouter = openrouter_key.startswith("sk-or-")
    cloudflare_token = _value(settings.jev_cloudflare_api_token) or _value(settings.cloudflare_api_token)
    account_id = settings.cloudflare_account_id or ""
    has_cloudflare = bool(cloudflare_token and account_id)
    api_key = _value(settings.jev_api_key)
    base_url = _value(settings.jev_api_base_url)

    def typesafe() -> JevProvider:
        return TypeSafeProvider(
            redact, api_key=typesafe_key, base_url=_value(settings.typesafe_base_url) or None, retry=retry
        )

    def openrouter() -> JevProvider:
        return OpenRouterProvider(redact, api_key=openrouter_key, retry=retry)

    def cloudflare() -> JevProvider:
        return CloudflareProvider(redact, api_token=cloudflare_token, account_id=account_id, retry=retry)

    def compatible() -> JevProvider:
        return CompatibleProvider(redact, api_key=api_key, base_url=base_url, retry=retry)

    match explicit:
        case "typesafe":
            if not typesafe_key:
                raise ProviderConfigError("JEV_PROVIDER=typesafe but TYPESAFE_API_KEY is not set.")
            return typesafe()
        case "openrouter":
            if not has_openrouter:
                raise ProviderConfigError(
                    "JEV_PROVIDER=openrouter but OPENROUTER_API_KEY is not set or not an sk-or- key."
                )
            return openrouter()
        case "vercel":
            raise ProviderConfigError(VERCEL_UNSUPPORTED)
        case "cloudflare":
            if not has_cloudflare:
                raise ProviderConfigError(
                    "JEV_PROVIDER=cloudflare but a Cloudflare API token (CLOUDFLARE_API_TOKEN or "
                    "JEV_CLOUDFLARE_API_TOKEN) and CLOUDFLARE_ACCOUNT_ID are not both set."
                )
            return cloudflare()
        case "compatible":
            missing = [name for name, value in (("JEV_API_KEY", api_key), ("JEV_API_BASE_URL", base_url)) if not value]
            if missing:
                verb = "are" if len(missing) > 1 else "is"
                raise ProviderConfigError(
                    f"JEV_PROVIDER=compatible but {' and '.join(missing)} {verb} not set. "
                    "JEV_MCP_MODEL is optional and defaults to jev-latest."
                )
            return compatible()
        case _:
            pass
    if typesafe_key:
        return typesafe()
    if has_openrouter:
        return openrouter()
    if has_cloudflare:
        return cloudflare()
    if _value(settings.ai_gateway_api_key):
        raise ProviderConfigError(VERCEL_UNSUPPORTED)
    if api_key and base_url:
        return compatible()
    raise ProviderConfigError(NO_CREDENTIALS)


def _value(secret: SecretStr | None) -> str:
    return "" if secret is None else secret.get_secret_value()
