# Codex

`jev-judge-mcp install` writes the `[mcp_servers.jev]` table in `~/.codex/config.toml` (ADR-0033). The TOML shape is in the README install section. The installer does not merge or enable the command hook.

For a separately configured Streamable HTTP connection, set an expanded `JEV_MCP_HTTP_TOKEN`
of at least 32 characters on the server and send it as a bearer token on every request, including
loopback. [HTTP setup](../../README.md#running-over-http-experimental)

The command hook is not the `jev_gate` MCP tool, which stays the completion gate (ADR-0035).

## Routing skill

Codex has no skill package. Paste `docs/skills/jev-mcp/SKILL.md` into the project's `AGENTS.md` for tool routing. Paste `src/jev_judge_mcp/skills/jev/SKILL.md` only when the task is building an app on the Jev API; its cookbook thresholds are not this server's defaults (`docs/reference/limits.md`). A connected client can read both at `jev-skill://jev-mcp/SKILL.md` and `jev-skill://jev/SKILL.md` instead of pasting. The on-demand rule is in `docs/agent-rules.md`.

## Matching results to caller IDs

Send unique IDs using ASCII letters, digits, dots, hyphens, and underscores. Use at most 64
characters and avoid leading or trailing underscores to receive the same IDs in results.
Collisions after sanitization return `invalid_arguments` before a provider request. See the
[caller ID rules](../tools.md#caller-ids) for reserved keys and generated IDs.

## Command hook

The hook is opt-in. It can deny or ask, and it can never allow. Empty stdout means the hook abstained and Codex keeps its own permission flow. Any other stdout is one JSON object whose `permissionDecision` is `deny` or `ask`.

The judgment is routed by the event's tool name (ADR-0076). A `Bash` action is judged by two questions in one provider call: what the command would do to material in the working directory (`read_only`, `reversible`, or `irreversible`), and whether destroying work is its purpose. A `Write` or `Edit` gets two checks computed in code first: a target outside the working directory is denied before the provider is even constructed, and so is content carrying a credential literal — a well-known token format, or a quoted high-entropy value assigned to a secret-named key. Ordinary code is never a hit and is judged as written. What survives both is judged by one credential-in-content question. Any other tool name keeps the generic allow/deny question. Every deny reason names the failure and its confidence or probability, and ends with a final-block notice telling the agent not to retry, split, or reroute the action.

`jev-judge-mcp install` does not merge [`gate.hooks.json`](gate.hooks.json) and does not enable it. The same fragment can be placed in `~/.codex/hooks.json` or a repo `.codex/hooks.json`. Codex runs that file after you trust it with `/hooks`. The sample command is `/absolute/path/to/jev-judge-mcp hook gate`. Real settings need the absolute path of the `jev-judge-mcp` executable. Do not use `uv run --directory`: that flag changes the working directory, so the hook judges the checkout instead of the caller's repo. `uv run --project <repo>` keeps the caller's cwd. The matcher is `Bash|Write|Edit`. The harness allows 30 seconds, with 25 for the provider call and its retries. The gate caps the event at 100,000 UTF-16 units before parsing or redacting it. An oversized event abstains by default and asks with `JEV_HOOK_REQUIRED=1`.

`JEV_GATE_STATE` is the environment variable `src/jev_judge_mcp/hook.py` reads when it builds judged state. Unset, or whitespace only, it is omitted. Otherwise the stripped value is the next line after the event's cwd and permission mode, before the proposed action. `redact_action` runs on the action description and input. It does not run on `JEV_GATE_STATE`. That text is sent to the provider, so it is not a place to put a key. The hook reads no further hook variable. The thresholds — the 0.5 / 0.4 confidence floors for the effect and fallback questions, 0.7 for the destructive-intent and credential questions — are constants in `src/jev_judge_mcp/hook.py`, not environment variables.

Invalid stdin, missing provider credentials, invalid settings, and unexpected local hook errors exit 0 with a stderr line and no decision. Set `JEV_HOOK_REQUIRED=1` when this hook is the enforcer so these failures ask (ADR-0065). A provider failure asks with reason `unreachable`.

## Completion hook

Codex hook matching is unverified against https://developers.openai.com/codex/hooks. Do not treat [`completion.hooks.json`](completion.hooks.json) as a Codex hook; its matcher is the Claude Code tool name `Bash`. It is not [`gate.hooks.json`](gate.hooks.json). `install` does not enable it. A missing credential fails open and is not a pass, unless `JEV_HOOK_REQUIRED=1` (ADR-0065), which asks. The command is the executable path, not `uv run --directory`. Use `jev-judge-mcp gate` until the Codex matcher is verified. The compact cut hook ([`compact.hooks.json`](compact.hooks.json)) is likewise unverified for Codex: it is written against Claude Code's SessionStart contract.

## Screen hook

`jev-judge-mcp hook screen` is a Claude Code PostToolUse annotator (`docs/harness/claude.md`). Codex PostToolUse matching is unverified against https://developers.openai.com/codex/hooks; do not treat [`screen.hooks.json`](screen.hooks.json) as a Codex hook. Use the CLI tools until the PostToolUse annotation shape is verified for Codex.