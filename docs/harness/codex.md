# Codex

`jev-judge-mcp install` writes the `[mcp_servers.jev]` table in `~/.codex/config.toml` (ADR-0033). The TOML shape is in the README install section. The installer does not merge or enable the command hook.

The command hook is not the `jev_gate` MCP tool, which stays the completion gate (ADR-0035).

## Routing skill

Codex has no skill package. Paste `docs/skills/jev-mcp/SKILL.md` into the project's `AGENTS.md`.

## Command hook

The hook is opt-in. It can deny or ask, and it can never allow. Empty stdout means the hook abstained and Codex keeps its own permission flow. Any other stdout is one JSON object whose `permissionDecision` is `deny` or `ask`.

`jev-judge-mcp install` does not merge [`gate.hooks.json`](gate.hooks.json) and does not enable it. The same fragment can be placed in `~/.codex/hooks.json` or a repo `.codex/hooks.json`. Codex runs that file after you trust it with `/hooks`. The sample command is `/absolute/path/to/jev-judge-mcp hook gate`. Real settings need the absolute path of the `jev-judge-mcp` executable. The matcher is `Bash|Write|Edit` and the timeout is 30 seconds.

`JEV_GATE_STATE` is the environment variable `src/jev_judge_mcp/hook.py` reads when it builds judged state. Unset, or whitespace only, it is omitted. Otherwise the stripped value is the next line after the event's cwd and permission mode, before the proposed action. `redact_action` runs on the action description and input. It does not run on `JEV_GATE_STATE`. That text is sent to the provider, so it is not a place to put a key. The hook reads no further hook variable. The 0.5 / 0.4 thresholds are constants in `src/jev_judge_mcp/hook.py`, not environment variables.

Stdin that is not a JSON object, or a missing provider credential, exits 0 with a stderr line and no decision. A provider failure asks, with reason `unreachable`. `JEV_HOOK_REQUIRED=1` is the opt-in that asks instead of staying silent (ADR-0065).

## Completion hook

Codex hook matching is unverified against https://developers.openai.com/codex/hooks. Do not treat [`completion.hooks.json`](completion.hooks.json) as a Codex hook; its matcher is the Claude Code tool name `Bash`. It is not [`gate.hooks.json`](gate.hooks.json). `install` does not enable it. A missing credential fails open and is not a pass. Use `jev-judge-mcp gate` until the Codex matcher is verified.
