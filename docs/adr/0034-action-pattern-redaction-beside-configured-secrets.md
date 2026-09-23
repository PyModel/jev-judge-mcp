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
