---
status: accepted
---
# Amendment to ADR-0035: opt-in required hook

ADR-0035 fail-opens when stdin is not JSON or credentials are missing, so an unconfigured hook does not freeze every watched command. A hook that an operator has marked as the enforcer must not go silent.

The flag applies to both `jev-judge-mcp hook gate` and `jev-judge-mcp completion-hook`. Both call `hook_required` and `fail_open_or_ask`, so the value and the ask text cannot drift.

## Decision

- The default is unchanged. Missing credentials, bad stdin, and a provider that is never called still exit 0 with stderr and no stdout decision.
- `JEV_HOOK_REQUIRED=1` changes those pre-call failures, and stdin over `hook gate`'s input cap, into `ask` with a typed reason. It does not print `allow`.
- On `completion-hook` the same flag asks for missing credentials, bad stdin, and a gate error that never reached the provider. A timeout, quota, or provider error after the call stays fail-open. A Bash command that is not `git push`, `gh pr create`, or `gh pr merge` stays silent. The hook still never prints `allow`.
- The flag is opt-in. `install` does not set it.
- Hook thresholds stay in `hook.py`. They are not copied into `policy/thresholds.py`.

## Consequences

- A machine that installed either hook before this flag still fail-opens.
- An operator who sets the flag and forgets the key gets `ask`, not silence, on both hooks.
