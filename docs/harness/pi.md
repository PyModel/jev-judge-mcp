# pi

Pi has no built-in MCP client. `jev-judge-mcp install` writes the `jev` entry in `~/.pi/agent/mcp.json` when `pi-mcp-adapter` is already installed. When the adapter is missing, that target is skipped and the installer prints `pi install npm:pi-mcp-adapter` (ADR-0033).

For Streamable HTTP, follow the [HTTP setup](../../README.md#running-over-http-experimental).

The installer uses `lifecycle: "eager"`, `toolPrefix: "none"` and the following `directTools` allow-list (ADR-0071). The adapter registers these names directly. Its `mcp` gateway also supports search, describe and status.

```json
[
  "jev_verify", "jev_screen", "jev_find", "jev_classify", "jev_decide",
  "jev_rerank", "jev_compare", "jev_extract", "jev_review", "jev_gate",
  "jev_score", "jev_file_judge", "jev_ask", "jev_files_judge", "jev_gate_range",
  "get_jev_mcp_skill_md"
]
```

This repository ships no native pi extension. The published tools stay the MCP server.

## Routing skill

See the [routing skill](../skills/jev-mcp/SKILL.md) and the [guide to both packaged skills](../../README.md#what-to-use-it-for).

See the [caller ID rules](../tools.md#caller-ids) for matching results to inputs.

## Hooks

See the [command-hook contract](hooks.md#command-hook). This repository ships no native Pi hook. The Claude Code screen, compact and completion protocols are unverified for Pi. Use `jev-judge-mcp gate` for completion.
