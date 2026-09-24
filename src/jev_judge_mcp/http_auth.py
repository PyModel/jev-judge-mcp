"""HTTP access control for the Streamable HTTP transport (ADR-0050).

A host the SDK does not protect is reachable without Host/Origin validation, and every tool call
spends the operator's provider credential, so the transport refuses to start unauthenticated:
only `127.0.0.1`, `localhost`, and `::1` are exempt from the token requirement, and every other
host requires `JEV_MCP_HTTP_TOKEN`. With a token configured, it is required on every request
whatever the host. stdio is untouched. Nothing here prints the token: error text names the
variables, never their values.
"""

import hmac
import logging

from pydantic import SecretStr
from starlette.datastructures import Headers
from starlette.responses import Response
from starlette.types import ASGIApp, Receive, Scope, Send

from jev_judge_mcp.settings import Settings

logger = logging.getLogger("jev_judge_mcp.http_auth")

HTTP_TOKEN_MIN_LENGTH = 32
"""Shortest acceptable `JEV_MCP_HTTP_TOKEN`: long enough that online guessing is hopeless."""

PROTECTED_HOSTS = ("127.0.0.1", "localhost", "::1")
"""Hosts exempt from the token: exactly the literals the SDK itself protects.

The SDK auto-enables Host/Origin (DNS-rebinding) validation only when the configured host equals
one of these strings (`mcp` `streamable_http_app`: `host in ("127.0.0.1", "localhost", "::1")`).
Any other loopback spelling — `127.9.9.9`, `0:0:0:0:0:0:0:1` — gets neither mechanism, so it
requires the token (ADR-0050). A pinning test fails CI if the SDK's list ever changes.
"""

_GENERATE_TOKEN = 'python -c "import secrets; print(secrets.token_urlsafe(32))"'  # noqa: S105 — an operator command, not a credential


def is_protected_host(host: str) -> bool:
    """True only for a host the SDK protects with Host/Origin validation (ADR-0050)."""
    return host in PROTECTED_HOSTS


def ensure_http_access_control(settings: Settings) -> None:
    """Refuse to start an HTTP server that would run without access control (ADR-0050).

    Runs before anything binds, so a misconfiguration is a non-zero exit with a clear error, not a
    live unauthenticated service. stdio never enters the gates. A configured token shorter than
    `HTTP_TOKEN_MIN_LENGTH` is refused on any host: it would be the only thing standing between
    the network and the operator's credential.
    """
    if settings.transport != "streamable-http":
        return
    token = settings.http_token
    if token is not None and len(token.get_secret_value()) < HTTP_TOKEN_MIN_LENGTH:
        raise SystemExit(
            f"JEV_MCP_HTTP_TOKEN is shorter than {HTTP_TOKEN_MIN_LENGTH} characters; refusing to start. "
            f"Generate a token with: {_GENERATE_TOKEN}"
        )
    if token is None and not is_protected_host(settings.http_host):
        raise SystemExit(
            f"JEV_MCP_HTTP_HOST={settings.http_host} has neither Host/Origin validation (the SDK grants "
            f"that only to {', '.join(PROTECTED_HOSTS)}) nor a token, so the HTTP transport would expose "
            "an unauthenticated server that spends the provider credential. Set JEV_MCP_HTTP_TOKEN to a "
            f"secret of at least {HTTP_TOKEN_MIN_LENGTH} characters to require a bearer token on every "
            f"request. Generate one with: {_GENERATE_TOKEN}"
        )


class BearerTokenMiddleware:
    """ASGI middleware requiring `Authorization: Bearer <JEV_MCP_HTTP_TOKEN>` on every request.

    The check runs before the MCP app sees the request, so a rejected call reaches no tool. The
    credential compares with `hmac.compare_digest` over the raw header bytes. A missing, malformed,
    or wrong token gets `401` with `WWW-Authenticate: Bearer` and a body that never echoes it.
    This is a plain shared-secret gate, not OAuth: the SDK's `token_verifier` + `AuthSettings` path
    needs an issuer URL and advertises authorization-server metadata this server does not have
    (ADR-0050).
    """

    def __init__(self, app: ASGIApp, token: SecretStr) -> None:
        self.app = app
        self._token = token.get_secret_value().encode()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        authorization = Headers(scope=scope).get("authorization", "")
        scheme, _, credential = authorization.partition(" ")
        if scheme.lower() != "bearer" or not hmac.compare_digest(credential.encode("latin-1"), self._token):
            logger.warning("rejected an HTTP request without a valid bearer token")
            response = Response("Unauthorized\n", status_code=401, headers={"WWW-Authenticate": "Bearer"})
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)
