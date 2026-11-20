---
status: accepted
---
# The HTTP transport refuses to run without access control

The critique of v0.1.1 reproduced the gap: with `JEV_MCP_TRANSPORT=streamable-http` and any
non-loopback `JEV_MCP_HTTP_HOST` — the standard way to publish a container port — the server was
an unauthenticated service. The SDK validates Host/Origin only when the host is literally
`127.0.0.1`, `localhost`, or `::1`, so a LAN bind answered forged-Host requests with 200, and any
network peer could `initialize` and call all eleven tools on the operator's provider key
(F5′, P1). ADR-0021 scoped the HTTP tier as a reliability question and never asked who may call
it; the sanctioned divergence entry inherited that blind spot. A second, smaller hole sat in the
redactor: a 1–2 character configured secret replaces every matching fragment of tool output and
logs (P3-7), because nothing bounded the length of what enters `Redactor`.

## Decision

`Settings` gains `http_token: SecretStr | None` from `JEV_MCP_HTTP_TOKEN`. Declaring it
`SecretStr` joins it to redaction automatically (ADR-0017). Two fail-closed startup gates run in
`main()` before anything binds, and a misconfiguration is one clear line on stderr and a non-zero
exit — never a live server:

- **Access control (F5′).** When the transport is `streamable-http`, a token is required on every
  host except the three the SDK itself protects — `127.0.0.1`, `localhost`, `::1`, exactly the
  literals for which the SDK auto-enables Host/Origin validation. Every other host would run with
  neither mechanism and must carry the token: other 127.0.0.0/8 addresses (`127.9.9.9`), other
  spellings of `::1` (`0:0:0:0:0:0:0:1`), `0.0.0.0`, `::`, LAN addresses, and any other name. A
  configured token shorter than 32 characters is refused on any host: it would be the only thing
  between the network and the operator's credential.
- **Redaction floor (P3-7).** Any configured secret the redactor receives — every `SecretStr`
  setting and the key-file key (ADR-0046) — must be at least 8 characters. The error names the
  variable, never the value.

With a token set, a small ASGI middleware wraps the app `streamable_http_app` returns and checks
`Authorization: Bearer <token>` with `hmac.compare_digest` before any MCP handling; a missing or
wrong credential gets `401` with `WWW-Authenticate: Bearer`. The three protected hosts keep the
SDK's automatic Host/Origin (DNS-rebinding) validation unchanged — a pinning test answers a
forged Host with 421 on each of them, so an SDK change to that list fails CI here. stdio is
untouched.

## Alternatives

- **The SDK's OAuth path (`token_verifier` + `AuthSettings`).** It exists for resource servers
  that speak OAuth 2.0: it requires an issuer URL and advertises authorization-server metadata
  this server does not have. A static shared token needs none of that machinery, so the middleware
  is ~20 lines instead of a phantom OAuth deployment.
- **Host/Origin allow-listing on non-loopback binds.** The Host header varies behind port
  publishing and proxies, so an allow-list is either wrong or so loose it protects nothing; and a
  required bearer token already defeats DNS rebinding, which is the attack that validation exists
  for. The protected hosts, where validation is on, need no token — the two mechanisms cover
  their own surfaces.

## Consequences

- A bind on any host outside `127.0.0.1` / `localhost` / `::1` without a token is a startup
  error, not a security incident.
- Divergence `http-bearer-token-required` is registered; `http-tier-b-experimental` names the
  access-control rule instead of leaving it implicit. The tier stays experimental: admission
  control, provider deadlines, and load-tested limits remain P9 work (ADR-0021).
- A secret shorter than 8 characters is a startup error naming its variable.
- `JEV_MCP_HTTP_TOKEN` is redacted wherever other secrets are (ADR-0017's derived tests cover it
  the moment it was declared). It never appears in an error message, a refusal, or a 401 body.
- The pinning tests are `tests/unit/test_http_auth.py` (the gates and the middleware in process,
  plus a forged-Host 421 on each exempt host so the SDK's protected list is pinned) and
  `tests/integration/test_http_auth.py` (a real loopback socket: refusal exits, 401/200, and
  the token's absence from stderr).

## Amendment (2026-11-19): HTTP authenticates on every bind address

Require `JEV_MCP_HTTP_TOKEN` on every Streamable HTTP bind, including loopback. Other local
processes may reach a loopback listener and act with its provider credential, local read
permissions, and enabled tools. Host and Origin validation addresses DNS rebinding and remains
in effect where the SDK enables it. It cannot distinguish one process on the host from another.

The startup gate refuses absent tokens, values shorter than 32 characters, and unexpanded
environment references even when they meet the length floor. Errors name the setting without
printing its value. All HTTP requests pass through the bearer middleware.

This amendment supersedes the port probe in ADR-0055. Startup binds and listens within its
existing retry budget before warming workers, then hands those open sockets to uvicorn. It sets
`SO_REUSEADDR` on POSIX to allow restarts during `TIME_WAIT`. Linux's bind rejects live wildcard
overlaps. On macOS and other non-Linux POSIX systems, startup makes a non-blocking connection
probe before binding, using loopback for a wildcard address. It gives a pending probe up to 50 ms
to report a result. A connected, reset, or still-pending probe uses the same port-taken diagnostic
as a bind conflict. A refused connection permits the bind attempt without a retry delay. IPv6
sockets remain IPv6-only. An unresolved address exits with a diagnostic naming `JEV_MCP_HTTP_HOST`.

On SIGINT or SIGTERM, uvicorn gives active requests two seconds to finish before cancelling
them, including any open event stream.
