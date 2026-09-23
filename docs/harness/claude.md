# Claude Code

`jev-judge-mcp install` registers the MCP server under the name `jev` in `~/.claude.json` (ADR-0033). Tool names in Claude Code are `mcp__jev__*`. The installer does not write permission allow-rules, and it does not merge or enable the command hook.

The command hook is not the `jev_gate` MCP tool, which stays the completion gate (ADR-0035).

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
      "mcp__jev__jev_score"
    ]
  }
}
```

`mcp__jev` allows the whole server in one rule. The installer does not write either form.

## Routing skill

Which tool fits a step is `docs/skills/jev-mcp/SKILL.md`. This repository ships no plugin manifest. A plugin entry is appropriate only when it points at that skill and embeds no machine path and no key.

## Command hook

The hook is opt-in. It can deny or ask, and it can never allow. Empty stdout means the hook abstained and Claude Code keeps its own permission flow. Any other stdout is one JSON object whose `permissionDecision` is `deny` or `ask`.

`jev-judge-mcp install` does not merge [`gate.hooks.json`](gate.hooks.json) and does not enable it. To turn the hook on, merge that fragment's `hooks` object into `~/.claude/settings.json` or a project's `.claude/settings.json`. The sample command is `/absolute/path/to/jev-judge-mcp hook gate`. Real settings need the absolute path of the `jev-judge-mcp` executable, because the hook's working directory is the project. The matcher is `Bash|Write|Edit` and the timeout is 30 seconds, the same budget `src/jev_judge_mcp/hook.py` passes to the provider call.

`JEV_GATE_STATE` is the environment variable `src/jev_judge_mcp/hook.py` reads when it builds judged state. Unset, or whitespace only, it is omitted. Otherwise the stripped value is the next line after the event's cwd and permission mode, before the proposed action. `redact_action` runs on the action description and input. It does not run on `JEV_GATE_STATE`. That text is sent to the provider, so it is not a place to put a key. Provider URL, model, and credentials stay the process configuration in ADR-0008. The hook loads those the same way the server does. It reads no further hook variable, and the 0.5 / 0.4 thresholds are constants in `src/jev_judge_mcp/hook.py`, not environment variables.

Stdin that is not a JSON object, or a missing provider credential, exits 0 with a stderr line and no decision. A provider failure asks, with reason `unreachable`.
