"""HTTP access control for the Streamable HTTP transport (ADR-0050).

Every HTTP request uses the operator's bearer token, including requests on loopback.
Startup errors name the setting without exposing its value.
"""

import hmac
import logging

from pydantic import SecretStr
from starlette.datastructures import Headers
from starlette.responses import Response
from starlette.types import ASGIApp, Receive, Scope, Send

from jev_judge_mcp.settings import Settings, is_unexpanded_reference

logger = logging.getLogger("jev_judge_mcp.http_auth")

HTTP_TOKEN_MIN_LENGTH = 32
"""Shortest acceptable `JEV_MCP_HTTP_TOKEN`: long enough that online guessing is hopeless."""

_GENERATE_TOKEN = 'python -c "import secrets; print(secrets.token_urlsafe(32))"'  # noqa: S105 — an operator command, not a credential


def ensure_http_access_control(settings: Settings) -> None:
    """Require a valid bearer token before the HTTP transport starts (ADR-0050)."""
    if settings.transport == "streamable-http":
        require_http_token(settings)


def require_http_token(settings: Settings) -> SecretStr:
    token = settings.http_token
    if token is None:
        problem = f"is required for HTTP and must be at least {HTTP_TOKEN_MIN_LENGTH} characters"
    elif is_unexpanded_reference(token.get_secret_value()):
        problem = "holds an unexpanded environment reference"
    elif len(token.get_secret_value()) < HTTP_TOKEN_MIN_LENGTH:
        problem = f"is shorter than {HTTP_TOKEN_MIN_LENGTH} characters"
    else:
        return token
    raise SystemExit(f"JEV_MCP_HTTP_TOKEN {problem}; refusing to start. Generate a token with: {_GENERATE_TOKEN}")


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
        if scope["type"] == "lifespan":
            await self.app(scope, receive, send)
            return
        if scope["type"] != "http":
            # No websocket route exists; a handshake is refused rather than passed through unchecked.
            await receive()
            logger.warning("rejected a %s connection: only authenticated HTTP is served", scope["type"])
            await send({"type": "websocket.close", "code": 1008})
            return
        authorization = Headers(scope=scope).get("authorization", "")
        scheme, _, credential = authorization.partition(" ")
        if scheme.lower() != "bearer" or not hmac.compare_digest(credential.encode("latin-1"), self._token):
            logger.warning("rejected an HTTP request without a valid bearer token")
            response = Response("Unauthorized\n", status_code=401, headers={"WWW-Authenticate": "Bearer"})
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)
