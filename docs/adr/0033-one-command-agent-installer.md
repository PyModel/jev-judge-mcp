---
status: accepted
---

# One command installs the `jev` MCP entry into local agent configs

> Partially superseded. ADR-0051 supersedes the pre-publication checkout-only pin. ADR-0084 removes the TypeSafe extra from generated launches.

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
- **Concurrent config edits.** Creating a config uses an exclusive hard link. Updating one exchanges its
  name atomically with a staged file on Linux or macOS. The installer checks the actual displaced bytes
  before deleting the staging file. If an editor saved in the final commit window, install reports both
  paths for reconciliation and exits with an error. Its journal identifies the displaced file after an
  interruption. A filesystem without atomic exchange support refuses an update.
- **File limits.** Installer config and state reads require regular files of at most 16 MiB. Opening
  them in nonblocking mode lets the installer reject a pipe while holding its state lock.

## Consequences

- `tomlkit` is a runtime dependency because the installer ships in the same package as the server.
- Re-running without `--desktop-key` does not strip a literal key this installer already stored, and does not rewrite a file whose entry already matches.
- A file that fails to parse, or whose server map is not an object or table, is left unchanged.
- Project-scope configs, Windows, keychain storage, and a PyPI release are out of scope.

## Amendment (2026-09-24): Cursor is a target, and reads tolerate a UTF-8 BOM

- **Cursor.** `~/.cursor/mcp.json` gains the same terminal-agent entry as pi, never a literal,
  except the reference is `${env:TYPESAFE_API_KEY}`: Cursor's config interpolation expands only
  `${env:NAME}` and would pass `${TYPESAFE_API_KEY}` through verbatim (corrected 2026-10-15). Detection is the `~/.cursor` directory or the
  `cursor` binary on PATH; the check line says to restart Cursor, because no CLI query command is
  documented.
- **BOM tolerance.** An existing config is decoded `utf-8-sig`: a UTF-8 BOM (files authored on or
  copied from Windows) neither fails parsing — which would have blocked the install — nor leaks
  into the rewritten file. The rewritten file simply no longer carries it. Everything else about
  failed parses and foreign entries is unchanged.

## Amendment (2026-11-19): preserve neighboring config and bound private backups

- JSON and JSONC removal splices the named property and one adjacent comma using source
  offsets. JSONC trailing commas, comments outside the edited property, and CRLF line
  endings are supported. Duplicate keys along the edited path must be made unique before
  installation. OpenCode distinguishes a v1 server named `servers` from its v2 map by shape.
- Codex edits keep comment headings adjacent to the following table. The installer parses
  its proposed output before publishing and checks the requested entry, the rest of the
  parsed config, and the affected comment structure. A validation error leaves that
  target's config untouched.
- Recovery compares the staged digest with the prepared replacement. An unexchanged
  replacement can be discarded even when the agent has since rewritten the config.
  Completed recovery removes the staging file and preserves other targets' records.
  A displaced concurrent edit still remains available for reconciliation.
- Installer state and backups use the shared private-directory checks. After a successful
  config write, the installer retains the three newest backups for that target. Pi adapter
  detection uses the same 16 MiB regular-file limit and BOM-tolerant decoding as config reads.

## Amendment (2026-11-19): Installer specs use the base package

ADR-0084 removes the provider extra. The installer writes a bare pinned distribution or
absolute checkout path and migrates owned entries written with the previous extra suffix.

## Amendment (2026-11-19): Installer JSON uses the shared decoder

Agent config, Pi settings, and installer state reject nonstandard constants (`NaN`,
`Infinity`, and `-Infinity`) and excessive nesting through the shared decoder. Agent JSON
and JSONC retain UTF-8 BOM support and JSONC retains comments and trailing commas. A bad
agent config, including invalid UTF-8, stays untouched and gets a one-line parse diagnostic.
Unreadable or malformed Pi settings are treated as adapter unavailable and keep the
existing adapter hint. Unreadable or malformed installer state blocks writes.

## Amendment (2026-11-19): one entry point for subcommands and help

The console script and `python -m jev_judge_mcp` both enter `__main__.main`. One ordered
table maps each command spelling to its lazy handler and help text. Only the fallback loads
the MCP server. Missing and unrecognized commands take that path, as before. The POSIX check
precedes dispatch, and the logging and failure policies for judgments and hooks run before
their handlers. Calibration calls `calibrate.main` directly.
