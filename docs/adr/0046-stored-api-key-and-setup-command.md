---
status: accepted
---
# A stored API key and a `setup` command onboard TypeSafe without an export

Amends ADR-0008: credentials still come only from process configuration, and no MCP tool argument
can set or influence one — that rule is unchanged. This decision adds one more source *beside* the
environment: a key file the server itself writes, through a dedicated command.

## Decision

- **`jev-judge-mcp setup`** is a new subcommand (like `install`, `doctor`, `hook`; no arguments
  still start the stdio server). It takes the key from `TYPESAFE_API_KEY` or a hidden prompt,
  never from argv — argv lands in shell history and process listings. It proves the key with one
  live noul call against TypeSafe before anything is written; a rejected key stores nothing and
  exits 1. The verify call is bounded (30 s): the CLI is not the parity-governed tool path, so it
  may not hang forever, unlike the product tier's registered no-deadline property.
- **Storage.** The key is written to `$JEV_MCP_KEY_FILE`, else `$XDG_CONFIG_HOME/jev-mcp/key`,
  else `~/.config/jev-mcp/key`: atomic write (temp + `os.replace` + fsync), file mode 0600,
  directory 0700, one trailing newline. The file is read as `utf-8-sig` so a stray BOM cannot
  become part of the key. A filesystem that cannot express the mode keeps the key anyway
  (ADR-0032 is POSIX-only; the chmod is best-effort).
- **Resolution.** `TYPESAFE_API_KEY` always wins; the file is consulted only when the variable is
  unset, and it fills the `typesafe` slot only (auto resolution and `JEV_PROVIDER=typesafe`). A
  blank or missing file resolves exactly as before, with the frozen error texts unchanged.
- **Redaction.** The stored value joins the resolver's `Redactor` set whenever it is read, so it
  is covered on every error path exactly like a configured secret (ADR-0017). The `setup` command
  proves the candidate key the same way: the verify provider carries the candidate in its
  redactor, and no output line ever contains the key.

## Why the key never becomes a tool argument

A `setup` *tool* that receives the key as an MCP argument was rejected on ADR-0008's own grounds:
tool arguments are model-controlled text, logged by clients and echoable by a model, and a
credential that crosses that boundary is one prompt-injection away from exfiltration. The command
form keeps the onboarding flow (verify once, store, restart-free use) without widening the attack
surface the ADR closes.

## Considered Options

- **Env-only, unchanged** — rejected: processes that cannot be restarted with an export (GUI
  agents, launchd-managed hosts) have no onboarding path at all.
- **A `setup` MCP tool** — rejected above.
- **Storing in a keychain** — out of scope for 1.0 (as ADR-0033 records for the installer).

## Consequences

- Resolution now reads one file when the env variable is absent. Every test environment pins
  `JEV_MCP_KEY_FILE` to a path that cannot exist (`tests/support/stdio.py`,
  `tests/support/replay.py`), so no fixture or parity replay depends on whether the host ran
  `setup`.
- `doctor` and `install` behavior are unchanged; the installer still writes references, not keys,
  for terminal agents.
- Registered divergence: `key-file-fallback` in `docs/reference/divergences.json`.
