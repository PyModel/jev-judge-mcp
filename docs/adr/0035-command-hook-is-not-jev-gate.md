---
status: accepted
---
# The command hook is not jev_gate

`jev_gate` is one of the ten MCP tools. It checks whether a piece of work finished. A proposed shell action is a different judgment, made before the action runs, and printing `allow` for it would loosen the harness that called the hook. The hook is an opt-in subprocess, `jev-judge-mcp hook gate`. It is not registered as a tool, and nothing named `jev_gate` implements it.

## Decision

- **Stdout.** Empty stdout means the hook abstains: the harness keeps its own permission flow. Otherwise stdout is one JSON object, `hookSpecificOutput.hookEventName` `PreToolUse`, `permissionDecision` `deny` or `ask`, and `permissionDecisionReason`. `permissionDecision` is never `allow`.
- **Reasons.** `unsure` means the provider answered under this hook's threshold, or the answer was not a usable allow/deny choice. `unreachable` means the provider raised, timed out, was cancelled, or returned no answer for the question. A pre-call failure (stdin that is not a JSON object, or no provider credentials) is fail-open: exit 0, one stderr line, no stdout decision.
- **Thresholds.** Reported confidence escalates below 0.5. A margin estimate, used only when the provider sent no confidence, escalates below 0.4. Those constants live in `src/jev_judge_mcp/hook.py`. They are not in `policy/thresholds.py`. The ten tools do not use them.
- **What is judged.** State is the event's cwd, its permission mode, optional `JEV_GATE_STATE` as the operator wrote it, then the proposed action. `redact_action` runs on the action's description and input (ADR-0034). It does not run on `JEV_GATE_STATE`. The hook does not log the action or the state. `JEV_GATE_STATE` is judged text, not a place to put a key.
- **Provider.** The hook calls `resolve_provider` and `evaluate`. The model is `resolve_model` (ADR-0008). There is no new provider and no mock provider in the resolver. The process passes a 30 second timeout to `evaluate` so a hung provider becomes `unreachable`. That timeout is this process's budget. `Runtime.ask` still passes no deadline (ADR-0021). The hook does not add a retry loop.
- **POSIX.** `main` calls the existing `require_posix` before the installer, this hook, and the server (ADR-0032). The hook does not carry a second platform check.
- **Install.** `jev-judge-mcp install` writes MCP entries. It does not enable this hook. An operator who wants it adds the command in the harness.

Fail-ask on bad stdin or missing credentials was the alternative: every watched command would wait for a person until the hook was configured. A missing key would then freeze every Bash call. Fail-open is limited to that pre-call case. A provider error is not fail-open, because silence there would read as a pass.

## Consequences

- Abstaining is not an allow decision from this hook. The harness's own rule still applies.
- The ten tools' thresholds, fail-closed answers, and `tools/list` shape are unchanged.
- Tests inject a fake provider. They do not add `JEV_PROVIDER=mock`.
