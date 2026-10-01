# pi

Pi has no built-in MCP client. `jev-judge-mcp install` writes the `jev` entry in `~/.pi/agent/mcp.json` when `pi-mcp-adapter` is already installed. When the adapter is missing, that target is skipped and the installer prints `pi install npm:pi-mcp-adapter` (ADR-0033).

The adapter exposes the server two ways. The published tools are registered directly and eagerly (`lifecycle: "eager"`, `directTools: true`, `toolPrefix: "none"` in the entry the installer writes): they appear in the model's initial tool list under their published names (`jev_verify`, ...), callable like any builtin. The `mcp` gateway tool still works for search, describe, and status. Inside pi the tools are not named `mcp__jev__*`; before this eager/direct entry, a lazy proxy-only server left them invisible until the model ran the gateway dance itself, and recorded agent runs show it never starts it (ADR-0036).

This repository ships no native pi extension. The published tools stay the MCP server.

## Routing skill

Which tool fits a step is `docs/skills/jev-mcp/SKILL.md`. Building an app on the Jev API, rather than calling these tools, is `src/jev_judge_mcp/skills/jev/SKILL.md`. A connected client reads them at `jev-skill://jev-mcp/SKILL.md` and `jev-skill://jev/SKILL.md`. Do not copy the `jev` skill's cookbook thresholds onto these tools; this server's defaults are `docs/reference/limits.md`. The on-demand rule is in `docs/agent-rules.md`.

The command hook is not the `jev_gate` MCP tool, which stays the completion gate (ADR-0035).

## Reading deduplicated ids

`jev_find`, `jev_verify`, and `jev_gate` sanitize and de-duplicate caller ids. When the same id is sent on several items, the first occurrence in caller order keeps the sent id and later occurrences get `_1`, `_2`, …; an unsuffixed id in a row therefore names the first physical item sent under that id. The payload's `renamed_ids` maps the sent id to the returned id of its last renamed occurrence (ADR-0062).

## Command hook

The hook is opt-in. It can deny or ask, and it can never allow. Empty stdout means the hook abstained. Any other stdout is one JSON object whose `permissionDecision` is `deny` or `ask`.

The judgment is routed by the event's tool name (ADR-0076). A `Bash` action is judged by two questions in one provider call: what the command would do to material in the working directory (`read_only`, `reversible`, or `irreversible`), and whether destroying work is its purpose. A `Write` or `Edit` gets two checks computed in code first: a target outside the working directory is denied before the provider is even constructed, and so is content carrying a credential literal — a well-known token format, or a quoted high-entropy value assigned to a secret-named key. Ordinary code is never a hit and is judged as written. What survives both is judged by one credential-in-content question. Any other tool name keeps the generic allow/deny question. Every deny reason names the failure and its confidence or probability, and ends with a final-block notice telling the agent not to retry, split, or reroute the action.

`jev-judge-mcp install` does not merge [`gate.hooks.json`](gate.hooks.json) and does not enable it. That fragment is the sample for a harness that runs a PreToolUse command hook. pi has no extension in this repo that turns the hook on. The sample command is `/absolute/path/to/jev-judge-mcp hook gate`. Real settings need the absolute path of the `jev-judge-mcp` executable. Do not use `uv run --directory`: that flag changes the working directory, so the hook judges the checkout instead of the caller's repo. `uv run --project <repo>` keeps the caller's cwd. The matcher is `Bash|Write|Edit` and the timeout is 30 seconds.

The completion-hook protocol for pi is unverified. Do not treat [`completion.hooks.json`](completion.hooks.json) as a pi hook. The CLI `jev-judge-mcp gate` is the path that does not need a hook. `JEV_MCP_MODEL` pins the model. The compact cut hook ([`compact.hooks.json`](compact.hooks.json)) is likewise unverified for pi: it is written against Claude Code's SessionStart contract.

`jev-judge-mcp hook screen` is a Claude Code PostToolUse annotator (`docs/harness/claude.md`). The PostToolUse annotation protocol for pi is unverified, and this repo ships no pi hook that would run it. The CLI tools stay the path that does not need a hook.

`JEV_GATE_STATE` is the environment variable `src/jev_judge_mcp/hook.py` reads when it builds judged state. Unset, or whitespace only, it is omitted. Otherwise the stripped value is the next line after the event's cwd and permission mode, before the proposed action. `redact_action` runs on the action description and input. It does not run on `JEV_GATE_STATE`. That text is sent to the provider, so it is not a place to put a key. The hook reads no further hook variable. The thresholds — the 0.5 / 0.4 confidence floors for the effect and fallback questions, 0.7 for the destructive-intent and credential questions — are constants in `src/jev_judge_mcp/hook.py`, not environment variables.
