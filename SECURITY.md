# Security

## Where judged state goes

A tool call's State is its arguments. The server sends that State to the configured provider (TypeSafe, OpenRouter, Cloudflare, or a compatible endpoint) as the judgment request. Provider URL, model, and credentials are process configuration, read once from the environment (`docs/adr/0008-provider-endpoints-and-credentials-are-process-config-only.md`). No tool argument sets them.

With `JEV_MCP_CACHE` on, the server also writes each request's State and its recorded answers to the response cache — `JEV_MCP_CACHE_DIR`, else `~/.cache/jev-mcp` (`docs/adr/0047-optional-response-cache-replays-verbatim.md`). Cache entries are mode 0600 inside a 0700 directory, like the key file: an entry holds the judged State. The cache is off by default; delete the directory to clear it.

The server does not write State anywhere else. Logs go to stderr. Telemetry spans record counts and labels. Argument text is included only when `JEV_MCP_TELEMETRY_PAYLOADS` is on, and then only in the span payload. The log handler still redacts configured secrets from that line.

## Error redaction

Configured secret values are removed from error text and from log records. The set is every `SecretStr` field on Settings plus a stored API key when one is read (`docs/adr/0017-redaction-secret-set-derives-from-settings-schema.md`).

## Keys

A key configured in the environment is redacted from logs and from provider error text, so it is not logged. The environment is the only place the server looks first: `TYPESAFE_API_KEY` unset, the resolver falls back to the key file `jev-judge-mcp setup` writes (`~/.config/jev-mcp/key`, or `JEV_MCP_KEY_FILE`), mode 0600 inside a 0700 directory (`docs/adr/0046-stored-api-key-and-setup-command.md`). The variable always wins, and the stored value is redacted like a configured secret. The server does not read a dotenv file. It never prints the stored value; `jev-judge-mcp doctor` reports only present or absent.

The installer keeps its own state file (`~/.local/state/jev-mcp/install.json`, mode 0600 in a 0700 directory) holding a hash of the entry it wrote, never a key. A key written into a GUI app's own config by `install --desktop-key` is reported when that file is still readable by group or others.

Pattern redaction of command text runs in the opt-in command hook, `jev-judge-mcp hook gate` (`docs/adr/0034-action-pattern-redaction-beside-configured-secrets.md`, `docs/adr/0035-command-hook-is-not-jev-gate.md`). `install` does not enable that hook. The tools do not call it. With no credentials the hook prints nothing and exits 0. On a provider failure it asks. `JEV_GATE_STATE` is sent as the operator wrote it and is not a place to put a key.

## Reporting a vulnerability

Report a vulnerability in private with a GitHub Security Advisory on [PyModel/jev-judge-mcp](https://github.com/PyModel/jev-judge-mcp/security/advisories/new).
