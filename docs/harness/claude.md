# Claude Code

`jev-judge-mcp install` registers the MCP server under the name `jev` in `~/.claude.json` (ADR-0033). Tool names in Claude Code are `mcp__jev__*`. The installer does not write permission allow-rules, and it does not merge or enable the command hook.

For a separately configured Streamable HTTP connection, set an expanded `JEV_MCP_HTTP_TOKEN`
of at least 32 characters on the server and send it as a bearer token on every request, including
loopback. [HTTP setup](../../README.md#running-over-http-experimental)

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

Which tool fits a step is `docs/skills/jev-mcp/SKILL.md`. Building an app on the Jev API, rather than calling these tools, is `src/jev_judge_mcp/skills/jev/SKILL.md`. A connected client reads them at `jev-skill://jev-mcp/SKILL.md` and `jev-skill://jev/SKILL.md`. Do not copy the `jev` skill's cookbook thresholds onto these tools; this server's defaults are `docs/reference/limits.md`. The on-demand rule is in `docs/agent-rules.md`. This repository ships no plugin manifest. A plugin entry is appropriate only when it points at one of those skills and embeds no machine path and no key.

## Matching results to caller IDs

Send unique IDs using ASCII letters, digits, dots, hyphens, and underscores. Use at most 64
characters and avoid leading or trailing underscores to receive the same IDs in results.
Collisions after sanitization return `invalid_arguments` before a provider request. See the
[caller ID rules](../tools.md#caller-ids) for reserved keys and generated IDs.

## Command hook

The hook is opt-in. It can deny or ask, and it can never allow. Empty stdout means the hook abstained and Claude Code keeps its own permission flow. Any other stdout is one JSON object whose `permissionDecision` is `deny` or `ask`.

The judgment is routed by the event's tool name (ADR-0076). A `Bash` action is judged by two questions in one provider call: what the command would do to material in the working directory (`read_only`, `reversible`, or `irreversible`), and whether destroying work is its purpose. A `Write` or `Edit` gets two checks computed in code first: a target outside the working directory is denied before the provider is even constructed, and so is content carrying a credential literal — a well-known token format, or a quoted high-entropy value assigned to a secret-named key. Ordinary code is never a hit and is judged as written. What survives both is judged by one credential-in-content question. Any other tool name keeps the generic allow/deny question. Every deny reason names the failure and its confidence or probability, and ends with a final-block notice telling the agent not to retry, split, or reroute the action.

`jev-judge-mcp install` does not merge [`gate.hooks.json`](gate.hooks.json) and does not enable it. To turn the hook on, merge that fragment's `hooks` object into `~/.claude/settings.json` or a project's `.claude/settings.json`. The sample command is `/absolute/path/to/jev-judge-mcp hook gate`. Real settings need the absolute path of the `jev-judge-mcp` executable, because the hook's working directory is the project. Do not use `uv run --directory`: that flag changes the working directory, so the hook judges the checkout instead of the caller's repo. `uv run --project <repo>` keeps the caller's cwd. The matcher is `Bash|Write|Edit`. The harness allows 30 seconds, with 25 for the provider call and its retries. The gate caps the event at 100,000 UTF-16 units before parsing or redacting it. An oversized event abstains by default and asks with `JEV_HOOK_REQUIRED=1`.

`JEV_GATE_STATE` is the environment variable `src/jev_judge_mcp/hook.py` reads when it builds judged state. Unset, or whitespace only, it is omitted. Otherwise the stripped value is the next line after the event's cwd and permission mode, before the proposed action. The hook scrubs configured secrets and recognized credential literals from the state before sending it to the provider. Command-pattern rules apply to the action description and input. Provider URL, model, and credentials stay the process configuration in ADR-0008. The hook loads those the same way the server does. It reads no further hook variable, and the thresholds — the 0.5 / 0.4 confidence floors for the effect and fallback questions, 0.7 for the destructive-intent and credential questions — are constants in `src/jev_judge_mcp/hook.py`, not environment variables.

Invalid stdin, missing provider credentials, invalid settings, and unexpected local hook errors exit 0 with a stderr line and no decision. Set `JEV_HOOK_REQUIRED=1` when this hook is the enforcer so these failures ask (ADR-0065). A provider failure asks with reason `unreachable`.

## Screen hook

`jev-judge-mcp hook screen` is a separate opt-in PostToolUse annotator, not `hook gate`. It reads the event, judges the first `SCREEN_INPUT_CHARS` (6,000 UTF-16 code units) of `tool_response` with one noul question — injected instructions that try to redirect the agent from its task or the user's request — and, at `SCREEN_FLAG_AT` (0.7) or above, returns one JSON object whose `hookSpecificOutput.additionalContext` Claude Code inserts next to the tool result as a system reminder (PostToolUse decision control, https://code.claude.com/docs/en/hooks). It never returns `decision: "block"` and never returns `updatedToolOutput`: the tool result reaches Claude unchanged. A Read of the operator's instruction files — `AGENTS.md`, `CLAUDE.md`, any `SKILL.md` — abstains before any provider call (ADR-0077): documentation the agent was pointed at is not a flag. Empty, whitespace-only, or binary output (a NUL byte in the judged prefix) also skips without a provider call.

[`screen.hooks.json`](screen.hooks.json) is the sample fragment. `install` does not merge or enable it; merge its `hooks` object into `~/.claude/settings.json` or a project's `.claude/settings.json` to turn it on. The matcher is `Bash|Read|WebFetch|WebSearch`. The harness allows 30 seconds, with 25 for the provider call and its retries. The executable-path and `uv run --project` rules are the command hook's, above.

Every failure mode is silence: stdin that is not a JSON object, a `tool_response` with no text, a missing provider credential, and any provider failure all exit 0 with empty stdout and one stderr line. `JEV_HOOK_REQUIRED` does not apply: a screen cannot ask, so the flag has no second mode here (ADR-0077). The hook scrubs configured secrets, recognized credential literals, and command patterns before sending the judged text to the provider, and neither the output nor the judgment is logged.

## Compact cut hook

`jev-judge-mcp hook compact-cut` runs after a compaction. Claude Code fires SessionStart with matcher `compact` (source `"compact"`) when auto-compact or `/compact` has completed, and the hook returns at most one `hookSpecificOutput.additionalContext` line naming the turn where the live work starts, so the summary keeps the live task. The PreCompact event cannot inject compaction instructions — its documented output contract is block-only (https://code.claude.com/docs/en/hooks) — so SessionStart's `additionalContext` is the one documented path.

The hook is opt-in and off by default: `jev-judge-mcp install` does not merge [`compact.hooks.json`](compact.hooks.json) and does not enable it. To turn it on, merge that fragment's `hooks` object into `~/.claude/settings.json` or a project's `.claude/settings.json`. The command is the absolute path of the `jev-judge-mcp` executable, not `uv run --directory`, for the same cwd reason as the command hook.

The harness allows 30 seconds, with 25 for the provider call and its retries. The hook reads at most the newest 8 MiB from a regular file at the event's `transcript_path` and keeps the newest 20 user turns — meta lines, the compaction summary itself, and slash-command wrappers or their local output are not user turns — clipping each to 1,000 UTF-16 units, and asks one Choice keyed by the real transcript turn ids. Those numbers are constants in `src/jev_judge_mcp/hook_compact.py` (ADR-0077), as the 0.5 / 0.4 confidence floors are constants in `src/jev_judge_mcp/hook.py`. What crosses to the provider is redacted (configured secrets, recognized credential literals, and command patterns); the returned line keeps the session's own words. A source other than `compact`, an unreadable or malformed transcript, fewer than two usable user turns, a provider failure, a missing or malformed answer, or a below-floor confidence all abstain: empty stdout, never a wrong turn. `JEV_HOOK_REQUIRED` does not apply — SessionStart has no ask decision to escalate to.

## Completion hook

`jev-judge-mcp completion-hook` is not `hook gate`. Claude Code PreToolUse matches tool names, not the command text ([hooks reference](https://code.claude.com/docs/en/hooks)). [`completion.hooks.json`](completion.hooks.json) therefore matches `Bash`. The program, not the matcher, keeps the command filter: `git push`, `gh pr create`, and `gh pr merge` reach the gate; any other Bash command exits 0 with no stdout. `install` does not enable the fragment. A missing credential fails open: empty stdout, `error.code` on stderr, never an allow. Set `JEV_HOOK_REQUIRED=1` when this hook is the enforcer: missing credentials, bad stdin, invalid settings, pre-provider gate failures, and unexpected local errors then ask (ADR-0065). The default stays fail-open. The command is the executable path in [`completion.hooks.json`](completion.hooks.json), not `uv run --directory`, for the same cwd reason as the command hook. The command reads `JEV_COMPLETION_DIFF`, `JEV_COMPLETION_CLAIMS`, and `JEV_COMPLETION_TESTS`; without `JEV_COMPLETION_DIFF`, a push uses `@{upstream}..HEAD` or the last commit. A PR for the current branch uses the merge base with the locally available PR base or `origin/HEAD`, so it still has a range after a push. An empty range abstains. A literal `cd` changes the shell directory for repository and evidence lookup. Git's `-C` selects the repository while evidence paths remain relative to the shell directory. An ambiguous target or unavailable PR base follows the failure policy above (ADR-0064 amendment).

`JEV_COMPLETION_CLAIMS` names a file inside the checkout. Its JSON form is
`{"request":"Ship the validated patch","claims":["The tests pass","The patch preserves existing callers"]}`.
A plain-text file uses one claim per non-empty line. The hook uses the matched Bash command,
with credential values redacted, as the request for plain text. The standalone `gate` command
takes `--request` with plain claims. Relative claims and test-log paths resolve from the shell's
working directory and must stay inside the checkout.

The completion hook subtracts time spent preparing local evidence from its 80-second judgment
budget. The 90-second harness timeout reserves 10 seconds for startup, cleanup, and the reply.
