---
status: accepted
---
# Provider URLs and credentials come only from process configuration

No MCP tool argument may set or influence a provider base URL, model slug, API key, account ID or header. These are read once from the environment at startup. Tool arguments come from a model that may be reading attacker-controlled text, so a `base_url` parameter would turn the `compatible` provider into an SSRF and credential-exfiltration primitive. The model name is likewise process-level (`JEV_MCP_MODEL`), matching the reference.

## Consequences

- Reject PRs that add per-call provider overrides, however convenient for testing; tests inject a provider object instead.
- Error text from every provider passes through secret redaction before reaching MCP. The reference redacts only `JEV_API_KEY`, and only on the `compatible` error body (`provider.ts:169-172`, quirk Q9). Python redacts every configured secret: `TYPESAFE_API_KEY`, `OPENROUTER_API_KEY`, `JEV_CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_API_TOKEN`, `AI_GATEWAY_API_KEY`, `JEV_API_KEY`, `TYPESAFE_BASE_URL`, and `JEV_API_BASE_URL`. The base URLs are in the set because they can carry userinfo, and the redactor runs on the whole exception string, which includes the request URL. Replacement is of the exact secret value, so a `Bearer` prefix is covered. This changes error text only (Sanctioned Divergence, Q9). The test matrix echoes each value into an error and expects `[redacted]`.

## Amendment (2026-09-24): one more process source — the stored key file

Credentials still come only from process configuration, and never from a tool argument. Beside the
environment there is now exactly one file: the key `jev-judge-mcp setup` writes
(`JEV_MCP_KEY_FILE`, else the XDG config default), consulted for the `typesafe` slot only when
`TYPESAFE_API_KEY` is unset, with the variable always winning. The stored value joins the
redaction set whenever it is read. Decision and rationale: ADR-0046.

