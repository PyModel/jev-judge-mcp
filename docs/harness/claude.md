# Claude Code

`jev-judge-mcp install` registers the MCP server under the name `jev` in `~/.claude.json` (ADR-0033). Tool names in Claude Code are `mcp__jev__*`. The installer does not write permission allow-rules.

For Streamable HTTP, follow the [HTTP setup](../../README.md#running-over-http-experimental).

## Allow-rules

Headless runs need an allow-rule. `acceptEdits` covers file edits. Add the published tools, or the one server rule `mcp__jev`, in `~/.claude/settings.json` or a project's `.claude/settings.json`:

```json
{
  "permissions": {
    "allow": [
      "mcp__jev__jev_verify",
      "mcp__jev__jev_screen",
      "mcp__jev__jev_find",
      "mcp__jev__jev_classify",
      "mcp__jev__jev_decide",
      "mcp__jev__jev_rerank",
      "mcp__jev__jev_compare",
      "mcp__jev__jev_extract",
      "mcp__jev__jev_review",
      "mcp__jev__jev_gate",
      "mcp__jev__jev_score",
      "mcp__jev__jev_file_judge",
      "mcp__jev__jev_ask",
      "mcp__jev__jev_files_judge",
      "mcp__jev__jev_gate_range"
    ]
  }
}
```

`mcp__jev` allows the whole server in one rule. The installer does not write either form. `doctor` checks regular settings files of up to 1 MiB.

`jev_ask` accepts commands only when the operator sets `JEV_ASK_COMMANDS=1` in the server's
launch environment. It validates commands and their options against closed read-only allowlists
and runs them directly as argv without a shell or harness prompt. An accepted command adds redacted
output to state and uses one provider call for the questions. See the `jev_ask` section of the
[tool contract](../tools.md) for allowed commands, allowed options, and limits.

## Routing skill

See the [routing skill](../skills/jev-mcp/SKILL.md) and the [guide to both packaged skills](../../README.md#what-to-use-it-for). This repository ships no plugin manifest. A plugin entry is appropriate only when it points at one of those skills and embeds no machine path and no key.

See the [caller ID rules](../tools.md#caller-ids) for matching results to inputs.

## Hooks

To enable the [command hook](hooks.md#command-hook), merge the `hooks` object from [gate.hooks.json](gate.hooks.json) into `~/.claude/settings.json` or a project's `.claude/settings.json`. The [hook guide](hooks.md) also covers the Claude Code screen, compact and completion fragments.
