# Security

## Where judged state goes

The server sends the judgment State and questions to the configured provider (TypeSafe, OpenRouter, Cloudflare, or a compatible endpoint). State can include supplied arguments, local file contents, Git diffs and, when command execution is enabled, command output. Review the material each tool reads before submitting confidential data. The process environment selects the endpoint, model and credentials.

The response cache is off by default. With `JEV_MCP_CACHE` enabled, the filename is a SHA-256 digest of the provider, endpoint or account scope, model, State and questions. The JSON record stores `answers`, `usage` (input and output tokens), `provider` and `model`. Answers can contain sensitive source material. Use a dedicated `JEV_MCP_CACHE_DIR`. The default is `$XDG_CACHE_HOME/jev-mcp` or `~/.cache/jev-mcp`. Entries use mode 0600 inside a 0700 directory. To clear them, stop processes using the cache and remove only files you have identified as Jev cache entries. Preserve unrelated files in custom directories.

Telemetry keeps recent spans in memory. By default, they contain counts, flags and labels. Enabling `JEV_MCP_TELEMETRY_PAYLOADS` includes argument text in recorded spans. With `JEV_MCP_LOG_LEVEL=DEBUG`, those payloads also reach stderr and any logs retained by the parent application. The handler masks configured secret values. Other credentials, personal information and source text can remain visible.

## Error redaction

Configured secret values are removed from error text and from log records. The set is every `SecretStr` field on Settings plus a stored API key when one is read (`docs/adr/0017-redaction-secret-set-derives-from-settings-schema.md`).

## Keys

A key configured in the environment is redacted from logs and from provider error text, so it is not logged. The environment is the only place the server looks first: `TYPESAFE_API_KEY` unset, the resolver falls back to the key file `jev-judge-mcp setup` writes (`~/.config/jev-mcp/key`, or `JEV_MCP_KEY_FILE`), mode 0600 inside a 0700 directory (`docs/adr/0046-stored-api-key-and-setup-command.md`). The variable always wins, and the stored value is redacted like a configured secret. The server does not read a dotenv file. It never prints the stored value; `jev-judge-mcp doctor` reports only present or absent.

The installer keeps its own state file (`~/.local/state/jev-mcp/install.json`, mode 0600 in a 0700 directory) holding a hash of the entry it wrote, never a key. Before modifying an existing config, the installer saves a private copy of the entire file under `$XDG_STATE_HOME/jev-mcp/backups` (default `~/.local/state/jev-mcp/backups`). These backups can contain credentials for other servers. Retain them for recovery and delete individual copies when they are no longer needed. A key written into a GUI app's own config by `install --desktop-key` is reported when that file is still readable by group or others.

Pattern redaction of command text runs in the opt-in command hook, `jev-judge-mcp hook gate` (`docs/adr/0034-action-pattern-redaction-beside-configured-secrets.md`, `docs/adr/0035-command-hook-is-not-jev-gate.md`). `install` does not enable that hook. The tools do not call it. With no credentials the hook prints nothing and exits 0. On a provider failure it asks. `JEV_GATE_STATE` is sent as the operator wrote it and is not a place to put a key.

## Reporting a vulnerability

Report a vulnerability in private with a GitHub Security Advisory on [PyModel/jev-judge-mcp](https://github.com/PyModel/jev-judge-mcp/security/advisories/new).
