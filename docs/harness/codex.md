# Codex

`jev-judge-mcp install` writes the `[mcp_servers.jev]` table in `~/.codex/config.toml` (ADR-0033). The TOML shape is in the README install section.

For Streamable HTTP, follow the [HTTP setup](../../README.md#running-over-http-experimental).

## Routing skill

Codex has no skill package. Paste `docs/skills/jev-mcp/SKILL.md` into the project's `AGENTS.md` for tool routing. Paste `src/jev_judge_mcp/skills/jev/SKILL.md` only when the task is building an app on the Jev API; its cookbook thresholds are not this server's defaults (`docs/reference/limits.md`). A connected client can read both at `jev-skill://jev-mcp/SKILL.md` and `jev-skill://jev/SKILL.md` instead of pasting.

See the [caller ID rules](../tools.md#caller-ids) for matching results to inputs.

## Hooks

Place the [command-hook fragment](gate.hooks.json) in `~/.codex/hooks.json` or a project's `.codex/hooks.json` and trust it with `/hooks`. See the [command-hook contract](hooks.md#command-hook). Codex matching for the Claude Code screen, compact and completion fragments is unverified. Use `jev-judge-mcp gate` for completion.
