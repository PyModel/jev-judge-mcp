# pi

Pi has no built-in MCP client. `jev-judge-mcp install` writes the `jev` entry in `~/.pi/agent/mcp.json` when `pi-mcp-adapter` is already installed. When the adapter is missing, that target is skipped and the installer prints `pi install npm:pi-mcp-adapter` (ADR-0033).

The adapter exposes the server two ways. The published tools are registered directly and eagerly (`lifecycle: "eager"`, `directTools: true`, `toolPrefix: "none"` in the entry the installer writes): they appear in the model's initial tool list under their published names (`jev_verify`, ...), callable like any builtin. The `mcp` gateway tool still works for search, describe, and status. Inside pi the tools are not named `mcp__jev__*`; before this eager/direct entry, a lazy proxy-only server left them invisible until the model ran the gateway dance itself, and recorded agent runs show it never starts it (ADR-0036).

This repository ships no native pi extension. The published tools stay the MCP server.

The command hook is not the `jev_gate` MCP tool, which stays the completion gate (ADR-0035).

## Command hook

The hook is opt-in. It can deny or ask, and it can never allow. Empty stdout means the hook abstained. Any other stdout is one JSON object whose `permissionDecision` is `deny` or `ask`.

`jev-judge-mcp install` does not merge [`gate.hooks.json`](gate.hooks.json) and does not enable it. That fragment is the sample for a harness that runs a PreToolUse command hook. pi has no extension in this repo that turns the hook on. The sample command is `/absolute/path/to/jev-judge-mcp hook gate`. Real settings need the absolute path of the `jev-judge-mcp` executable. The matcher is `Bash|Write|Edit` and the timeout is 30 seconds.

The completion-hook protocol for pi is unverified. Do not treat [`completion.hooks.json`](completion.hooks.json) as a pi hook. The CLI `jev-judge-mcp gate` is the path that does not need a hook. `JEV_MCP_MODEL` pins the model.

`JEV_GATE_STATE` is the environment variable `src/jev_judge_mcp/hook.py` reads when it builds judged state. Unset, or whitespace only, it is omitted. Otherwise the stripped value is the next line after the event's cwd and permission mode, before the proposed action. `redact_action` runs on the action description and input. It does not run on `JEV_GATE_STATE`. That text is sent to the provider, so it is not a place to put a key. The hook reads no further hook variable. The 0.5 / 0.4 thresholds are constants in `src/jev_judge_mcp/hook.py`, not environment variables.
