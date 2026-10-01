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
      "mcp__jev__jev_score",
      "mcp__jev__jev_file_judge"
    ]
  }
}
```

`mcp__jev` allows the whole server in one rule. The installer does not write either form.

## Routing skill

Which tool fits a step is `docs/skills/jev-mcp/SKILL.md`. Building an app on the Jev API, rather than calling these tools, is `src/jev_judge_mcp/skills/jev/SKILL.md`. A connected client reads them at `jev-skill://jev-mcp/SKILL.md` and `jev-skill://jev/SKILL.md`. Do not copy the `jev` skill's cookbook thresholds onto these tools; this server's defaults are `docs/reference/limits.md`. The on-demand rule is in `docs/agent-rules.md`. This repository ships no plugin manifest. A plugin entry is appropriate only when it points at one of those skills and embeds no machine path and no key.

## Reading deduplicated ids

`jev_find`, `jev_verify`, and `jev_gate` sanitize and de-duplicate caller ids. When the same id is sent on several items, the first occurrence in caller order keeps the sent id and later occurrences get `_1`, `_2`, …; an unsuffixed id in a row therefore names the first physical item sent under that id. The payload's `renamed_ids` maps the sent id to the returned id of its last renamed occurrence (ADR-0062).

## Command hook

The hook is opt-in. It can deny or ask, and it can never allow. Empty stdout means the hook abstained and Claude Code keeps its own permission flow. Any other stdout is one JSON object whose `permissionDecision` is `deny` or `ask`.

The judgment is routed by the event's tool name (ADR-0076). A `Bash` action is judged by two questions in one provider call: what the command would do to material in the working directory (`read_only`, `reversible`, or `irreversible`), and whether destroying work is its purpose. A `Write` or `Edit` gets two checks computed in code first: a target outside the working directory is denied before the provider is even constructed, and so is content carrying a credential literal — a well-known token format, or a quoted high-entropy value assigned to a secret-named key. Ordinary code is never a hit and is judged as written. What survives both is judged by one credential-in-content question. Any other tool name keeps the generic allow/deny question. Every deny reason names the failure and its confidence or probability, and ends with a final-block notice telling the agent not to retry, split, or reroute the action.

`jev-judge-mcp install` does not merge [`gate.hooks.json`](gate.hooks.json) and does not enable it. To turn the hook on, merge that fragment's `hooks` object into `~/.claude/settings.json` or a project's `.claude/settings.json`. The sample command is `/absolute/path/to/jev-judge-mcp hook gate`. Real settings need the absolute path of the `jev-judge-mcp` executable, because the hook's working directory is the project. Do not use `uv run --directory`: that flag changes the working directory, so the hook judges the checkout instead of the caller's repo. `uv run --project <repo>` keeps the caller's cwd. The matcher is `Bash|Write|Edit` and the timeout is 30 seconds, the same budget `src/jev_judge_mcp/hook.py` passes to the provider call.

`JEV_GATE_STATE` is the environment variable `src/jev_judge_mcp/hook.py` reads when it builds judged state. Unset, or whitespace only, it is omitted. Otherwise the stripped value is the next line after the event's cwd and permission mode, before the proposed action. `redact_action` runs on the action description and input. It does not run on `JEV_GATE_STATE`. That text is sent to the provider, so it is not a place to put a key. Provider URL, model, and credentials stay the process configuration in ADR-0008. The hook loads those the same way the server does. It reads no further hook variable, and the thresholds — the 0.5 / 0.4 confidence floors for the effect and fallback questions, 0.7 for the destructive-intent and credential questions — are constants in `src/jev_judge_mcp/hook.py`, not environment variables.

Stdin that is not a JSON object, or a missing provider credential, exits 0 with a stderr line and no decision. A provider failure asks, with reason `unreachable`. Set `JEV_HOOK_REQUIRED=1` only when this hook is the enforcer: those pre-call failures then ask instead of staying silent (ADR-0065). The default is unchanged.

## Screen hook

`jev-judge-mcp hook screen` is a separate opt-in PostToolUse annotator, not `hook gate`. It reads the event, judges the first `SCREEN_INPUT_CHARS` (6,000 UTF-16 code units) of `tool_response` with one noul question — injected instructions that try to redirect the agent from its task or the user's request — and, at `SCREEN_FLAG_AT` (0.7) or above, returns one JSON object whose `hookSpecificOutput.additionalContext` Claude Code inserts next to the tool result as a system reminder (PostToolUse decision control, https://code.claude.com/docs/en/hooks). It never returns `decision: "block"` and never returns `updatedToolOutput`: the tool result reaches Claude unchanged. A Read of the operator's instruction files — `AGENTS.md`, `CLAUDE.md`, any `SKILL.md` — abstains before any provider call (ADR-0077): documentation the agent was pointed at is not a flag. Empty, whitespace-only, or binary output (a NUL byte in the judged prefix) also skips without a provider call.

[`screen.hooks.json`](screen.hooks.json) is the sample fragment. `install` does not merge or enable it; merge its `hooks` object into `~/.claude/settings.json` or a project's `.claude/settings.json` to turn it on. The matcher is `Bash|Read|WebFetch|WebSearch` and the timeout is 30 seconds, the same budget `src/jev_judge_mcp/hook.py` passes its provider call. The executable-path and `uv run --project` rules are the command hook's, above.

Every failure mode is silence: stdin that is not a JSON object, a `tool_response` with no text, a missing provider credential, and any provider failure all exit 0 with empty stdout and one stderr line. `JEV_HOOK_REQUIRED` does not apply: a screen cannot ask, so the flag has no second mode here (ADR-0077). The judged text is sent to the provider with `redact_action` applied, and neither the output nor the judgment is logged.

## Compact cut hook

`jev-judge-mcp hook compact-cut` runs after a compaction. Claude Code fires SessionStart with matcher `compact` (source `"compact"`) when auto-compact or `/compact` has completed, and the hook returns at most one `hookSpecificOutput.additionalContext` line naming the turn where the live work starts, so the summary keeps the live task. The PreCompact event cannot inject compaction instructions — its documented output contract is block-only (https://code.claude.com/docs/en/hooks) — so SessionStart's `additionalContext` is the one documented path.

The hook is opt-in and off by default: `jev-judge-mcp install` does not merge [`compact.hooks.json`](compact.hooks.json) and does not enable it. To turn it on, merge that fragment's `hooks` object into `~/.claude/settings.json` or a project's `.claude/settings.json`. The command is the absolute path of the `jev-judge-mcp` executable, not `uv run --directory`, for the same cwd reason as the command hook.

The hook reads at most the newest 8 MiB of the event's `transcript_path` and keeps the newest 20 user turns — meta lines, the compaction summary itself, and slash-command wrappers or their local output are not user turns — clipping each to 1,000 UTF-16 units, and asks one Choice keyed by the real transcript turn ids. Those numbers are constants in `src/jev_judge_mcp/hook_compact.py` (ADR-0077), as the 0.5 / 0.4 confidence floors are constants in `src/jev_judge_mcp/hook.py`. What crosses to the provider is redacted (`redact_action` plus the credential-literal detector); the returned line keeps the session's own words. A source other than `compact`, an unreadable or malformed transcript, fewer than two usable user turns, a provider failure, a missing or malformed answer, or a below-floor confidence all abstain: empty stdout, never a wrong turn. `JEV_HOOK_REQUIRED` does not apply — SessionStart has no ask decision to escalate to.

## Completion hook

`jev-judge-mcp completion-hook` is not `hook gate`. Claude Code PreToolUse matches tool names, not the command text ([hooks reference](https://code.claude.com/docs/en/hooks)). [`completion.hooks.json`](completion.hooks.json) therefore matches `Bash`. The program, not the matcher, keeps the command filter: `git push`, `gh pr create`, and `gh pr merge` reach the gate; any other Bash command exits 0 with no stdout. `install` does not enable the fragment. A missing credential fails open: empty stdout, `error.code` on stderr, never an allow. Set `JEV_HOOK_REQUIRED=1` when this hook is the enforcer: missing credentials, bad stdin, and a gate error that never reached the provider then ask instead of staying silent (ADR-0065). The default stays fail-open. The command is the executable path in [`completion.hooks.json`](completion.hooks.json), not `uv run --directory`, for the same cwd reason as the command hook. The command reads `JEV_COMPLETION_DIFF`, `JEV_COMPLETION_CLAIMS`, and `JEV_COMPLETION_TESTS`.
