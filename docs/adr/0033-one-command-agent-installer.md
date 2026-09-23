---
status: accepted
---

# One command installs the `jev` MCP entry into local agent configs

Agents each keep their own MCP config, and several of those files are shared with a desktop app. A hand-edited snippet drifts, and a rewriter that parses and dumps TOML drops comments in `~/.codex/config.toml`. The installer is a subcommand of the existing console script so that running `jev-judge-mcp` with no arguments stays the stdio server.

## Decision

- **Entrypoint.** `jev-judge-mcp install` plans one named entry per selected agent, prints one summary, and writes after a single confirmation (`-y` skips it). `--dry-run` prints a redacted diff and writes nothing. `--remove` deletes an entry only when the state file shows this installer wrote it. `--agent` / `-a` and `--all` choose targets. No arguments to `jev-judge-mcp` still start the stdio server, and stdout stays protocol-only in that mode.
- **Name.** The config entry is `jev`. Tool names then read `mcp__jev__*`. The MCP `serverInfo.name` stays `jev-mcp` (ADR-0009).
- **Key.** Terminal entries store a reference to `TYPESAFE_API_KEY` (`${TYPESAFE_API_KEY}`, Codex `env_vars`, OpenCode `{env:TYPESAFE_API_KEY}`, or process inheritance). They never store the key. GUI apps do not see a shell export. Claude Desktop, and Codex or Pythinker when the matching desktop app is present, store the literal only after `--desktop-key`. The value comes from the environment or a hidden prompt, never from argv. Summaries, diffs, and the state file are redacted first (ADR-0017). The state file records a SHA-256 of the redacted entry.
- **GUI file mode.** New files are created mode 0600, and the state file is 0600 inside a 0700 directory. A write that stores the literal key sets the file to 0600: a looser mode is tightened, and bits that are already stricter stay stricter. A write that stores only a reference keeps the file's existing mode. Claude Desktop and other GUI apps rewrite their own config, so a mode of 0600 does not stick. After every write, and again on a later run, the installer reports a file that still holds the literal and is looser than 0600. It does not promise the mode survives that rewrite.
- **Codex TOML.** Edits go through `tomlkit`, which changes the `jev` table and leaves the rest of the file, including comments, in place. The stdlib `tomllib` reader cannot write the file back. Refusing the dependency would mean refusing Codex, because dumping with a lossy parser deletes comments other tools stored in `config.toml`.
- **OpenCode.** JSONC is edited in process. A comment outside the `jev` value is kept. A shape other than the v1 `mcp` map or the v2 `mcp.servers` map fails with a message and is not written. The installer does not shell out to `opencode mcp add`: that binary writes the user's real global config, and `--remove` still needs a local edit.
- **SDK.** `typesafe-sdk` stays an optional extra (ADR-0009). Every generated command is `uvx --from '<checkout>[typesafe]' jev-judge-mcp`, so the extra is selected at launch without becoming a required dependency of this package.
- **Not published yet.** Until `jev-judge-mcp` is on PyPI, the `--from` spec is the absolute path of this checkout plus `[typesafe]`. The installer refuses to write the bare package name `jev-mcp`, which is a different project (ADR-0009, ADR-0049).
- **Pi.** Pi has no built-in MCP. If `pi-mcp-adapter` is missing from `settings.json`, that target is not written and the installer prints `pi install npm:pi-mcp-adapter`. Other selected agents are still configured. The process exits non-zero.
- **Pythinker desktop.** A desktop share is detected only when a bundle's `CFBundleIdentifier` is `com.pythinker.desktop`. A path named `PyThinker.app` is not enough.

## Consequences

- `tomlkit` is a runtime dependency because the installer ships in the same package as the server.
- Re-running without `--desktop-key` does not strip a literal key this installer already stored, and does not rewrite a file whose entry already matches.
- A file that fails to parse, or whose server map is not an object or table, is left unchanged.
- Project-scope configs, Windows, keychain storage, and a PyPI release are out of scope.

## Amendment (2026-09-24): Cursor is a target, and reads tolerate a UTF-8 BOM

- **Cursor.** `~/.cursor/mcp.json` gains the same terminal-agent entry as pi (a
  `${TYPESAFE_API_KEY}` reference, never a literal). Detection is the `~/.cursor` directory or the
  `cursor` binary on PATH; the check line says to restart Cursor, because no CLI query command is
  documented.
- **BOM tolerance.** An existing config is decoded `utf-8-sig`: a UTF-8 BOM (files authored on or
  copied from Windows) neither fails parsing — which would have blocked the install — nor leaks
  into the rewritten file. The rewritten file simply no longer carries it. Everything else about
  failed parses and foreign entries is unchanged.

