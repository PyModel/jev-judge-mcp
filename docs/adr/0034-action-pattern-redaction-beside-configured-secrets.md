---
status: accepted
---
# Action-pattern redaction sits beside the configured-secret redactor

ADR-0017 redacts configured secret values (`Settings.secret_values()`, `Redactor` in `src/jev_judge_mcp/errors.py`) from error text and logs. A gated action is other text. A later command hook will hand the judge a command the caller did not write, and that command can carry a credential that is not in Settings. jev-use strips those values in `src/redact.ts` and sends caller-written state as it was written.

## Decision

`src/jev_judge_mcp/redact_action.py` ports that rule table. The replacement is `[redacted]`. A captured value that is only `$NAME`, `${NAME}`, `%NAME%`, or `$(...)` stays, so the judge still sees which secret the command names. A captured secret stops at a quote or a backslash, so a JSON `\"` is left intact. The module imports nothing from providers or tools. The command hook is the caller (ADR-0035).

Extending `Redactor` to run the same table was rejected. That class is the ADR-0008 / ADR-0017 path for configured values on error text. Folding pattern rules into it would change the ten tools' error text. The two mechanisms stay separate, and `secret_values()` is unchanged.

## Consequences

- The ten tools' error text is unchanged. `secret_values()` still means configured values only.
- The command hook is the caller (ADR-0035). The ten tools do not call `redact_action`.
- The capture stops at whitespace as well as at a quote or backslash, matching `src/redact.ts`. A reference is kept when that whole captured value is the reference.

## Amendment (2026-11-19): preserve action targets and bound redaction work

Credential prefixes for `sk-`, GitHub, Google and JWT tokens must start at a left boundary
outside ASCII alphanumerics, underscores and hyphens. The `sk-` rule also uses the credential
literal mixed-case and digit check. This keeps paths such as `task-runner-cache-2024-old` in the
action the Bash judge sees. The URL rule scans each scheme-like run once.

The command gate checks its 100,000 UTF-16-unit event ceiling before parsing or redacting in
both permission modes. A larger event abstains by default and asks when `JEV_HOOK_REQUIRED=1`.
Gate, screen, and compact-cut reserve five of their 30 harness seconds for local work and
the reply. Compact-cut reads only regular transcripts and caps each tail read at 8 MiB.

## Amendment (2026-11-19): redaction ownership and hook state

The `redaction` package owns the replacement marker and the three redaction rules.
`redaction.values` masks the configured exact values and owns log filtering.
`redaction.literals` detects credential literals in provider-bound state.
`redaction.action` retains the command-pattern rules. Hook state and ask state use the
per-call guard's exact-values-then-literals ordering. Hooks additionally apply the action
rules where they process commands, tool output, or transcript turns. The installer uses
the shared exact-value redactor for diagnostics.
