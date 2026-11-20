# ADR-0084 TypeSafe shares the HTTP provider

Accepted 2026-11-19.

## Context

TypeSafe used the optional `typesafe-sdk` 0.7.1 package and its `httpx2` client. The server had to
adapt the SDK's exceptions, disable its retries, capture raw bodies and headers, and duplicate
transport controls. It needed a separate DEBUG filter for SDK wire bodies and a startup import
probe. The SDK also consulted `TYPESAFE_*` environment variables outside Settings. The installer
added a `typesafe` extra to every launch spec.

## Decision

Use `HttpProvider` for TypeSafe. The installed SDK's `_core/constants.py`, `_core/endpoints.py`
(`prepare_system_one`), `_core/config.py` and `_core/client/aio/client.py` establish the request
contract. POST to the configured base URL with trailing slashes removed, followed by
`/v1/systemone`. Use `https://api.typesafe.ai` when the base URL is unset. Send
`Authorization: Bearer <key>`, `Accept: application/json`, and `Content-Type: application/json`.
The JSON fields are `state`, `model`, and `questions`, in that order. Send the requested model
as given. Use the shared client's User-Agent and omit SDK diagnostic headers.

Pass raw success bytes through JSON decoding and `parse_envelope` (ADR-0003). Keep the request
ID from the body or headers (ADR-0068) and report the requested model (`provider.ts:122`).
Use the shared status-error path and `Retry-After` handling. The shared client owns same-origin
redirects, refusal of credentials in endpoint URLs, identity encoding, response byte limits,
cancellation, and per-attempt timeouts. Settings supplies the provider configuration.

## Alternatives

- Keep the SDK. This retains an upstream client and its schema updates alongside the duplicate
  transport, startup and logging paths.
- Vendor the SDK schema. This removes the runtime dependency and creates a second schema to
  maintain beside the canonical question types and answer-envelope rules. The endpoint contract
  needs only three fields and the existing domain types can supply them.

## Consequences

The base package serves all four providers. The installer writes a bare pinned distribution
or absolute checkout path. Reinstalling migrates installer-owned entries with the old extra
suffix using the existing entry hash. Existing third-party entries retain their ownership.
The TypeSafe adapter drops `httpx2`. MCP SDK 2.2.0 still depends on `httpx2` for its own transport.

The provider contract exercises HTTP behavior for TypeSafe alongside the other adapters. The
server owns compatibility with future System One API changes. The orchestrator runs the
bounded live verification after landing.
