# Security

## Where judged state goes

The server sends the judgment State and questions to the configured provider (TypeSafe, OpenRouter, Cloudflare, or a compatible endpoint). State can include supplied arguments, local file contents, Git diffs and, when command execution is enabled, command output. Review the material each tool reads before submitting confidential data. The process environment selects the endpoint, model and credentials.

The response cache is off by default. With `JEV_MCP_CACHE` enabled, the filename is a SHA-256 digest of the provider, endpoint or account scope, model, State and questions. The JSON record stores `answers`, `usage` (token counts and whether the provider reported them), `provider`, `model`, and `request_id`. A request ID can link a local judgment to the provider’s request logs. Accepted IDs are nonempty printable ASCII with at most 1,024 characters. Answers can contain sensitive source material. Use a dedicated `JEV_MCP_CACHE_DIR`. The default is `$XDG_CACHE_HOME/jev-mcp` or `~/.cache/jev-mcp`. Entries use mode 0600 inside a 0700 directory. To clear them, stop processes using the cache and remove only files you have identified as Jev cache entries. Preserve unrelated files in custom directories.

Telemetry keeps recent spans in memory. By default, they contain counts, flags and labels. Enabling `JEV_MCP_TELEMETRY_PAYLOADS` includes argument text in recorded spans. At `JEV_MCP_LOG_LEVEL=DEBUG`, those opt-in payloads can reach stderr and logs retained by the parent application. Provider HTTP diagnostics omit request bodies even at DEBUG. The log handler masks configured secret values. Opt-in telemetry payloads can contain other credentials, personal information and source text.

## Error redaction

Configured secret values are removed from error text and from log records. The set is every `SecretStr` field on Settings plus a stored API key when one is read (`docs/adr/0017-redaction-secret-set-derives-from-settings-schema.md`).

## Keys

A key configured in the environment is redacted from logs and from provider error text, so it is not logged. If `TYPESAFE_API_KEY` is unset or empty, the resolver falls back to the key file `jev-judge-mcp setup` writes (`~/.config/jev-mcp/key`, or `JEV_MCP_KEY_FILE`), mode 0600 inside a 0700 directory (`docs/adr/0046-stored-api-key-and-setup-command.md`). A non-empty key in the variable takes precedence. The server accepts a regular key file owned by its effective user or root with no group or other write permission, including qualifying read-only mounts. A file that fails these checks is ignored with a warning that names the path and reason. The stored value is redacted like a configured secret. The server does not read a dotenv file. It never prints the stored value; `jev-judge-mcp doctor` reports only present or absent.

The installer keeps its own state file (`~/.local/state/jev-mcp/install.json`, mode 0600 in a 0700 directory) holding a hash of the entry it wrote, never a key. Before modifying an existing config, the installer saves a private copy of the entire file under `$XDG_STATE_HOME/jev-mcp/backups` (default `~/.local/state/jev-mcp/backups`). These backups can contain credentials for other servers. After a successful config write, the installer keeps the three newest backups for that target. Launch diagnostics scrub configured secrets, the stored key, prompted keys, and recognized credential literals before display. A key written into a GUI app's own config by `install --desktop-key` is reported when that file is still readable by group or others.

Pattern redaction of command text runs in the opt-in command hook, `jev-judge-mcp hook gate` (`docs/adr/0034-action-pattern-redaction-beside-configured-secrets.md`, `docs/adr/0035-command-hook-is-not-jev-gate.md`). `install` does not enable that hook. The tools do not call it. With no credentials or on an unexpected local failure, the hook exits 0 and emits a decision only when `JEV_HOOK_REQUIRED=1`, in which case it asks. On a provider failure it asks. With `JEV_HOOK_REQUIRED=1`, an internal tool failure makes the completion hook ask. `JEV_GATE_STATE` is sent as the operator wrote it and is not a place to put a key.

## jev_ask command execution

`jev_ask` runs commands only with `JEV_ASK_COMMANDS=1`. It executes a fixed allowlist of read-only
Git and file inspection commands directly as argv without a shell. It refuses unquoted shell
operators, options outside each command's closed allowlist, working-directory executables, and
arguments naming known secret stores or private directories. The child gets a scrubbed environment
and closed stdin. Both output streams are bounded and redacted. Git runs with fsmonitor, hooks, paging, external diffs,
and text conversion disabled. Commands read with the server account's filesystem permissions.
Only the caller's questions and their composed state go to the provider, in one call.
[Command and option allowlists](docs/tools.md#jev_ask--your-questions-over-composed-state).

## Reporting a vulnerability

Report a vulnerability in private with a GitHub Security Advisory on [PyModel/jev-judge-mcp](https://github.com/PyModel/jev-judge-mcp/security/advisories/new).
