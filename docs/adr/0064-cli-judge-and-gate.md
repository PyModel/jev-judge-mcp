---
status: accepted
---
# The CLI is the harness-agnostic path

MCP stays the product surface for clients that speak it. A client that cannot is not a reason to fork policy. `jev-judge-mcp judge` and `jev-judge-mcp gate` call the same tools.

## Decision

- `judge <tool>` reads one JSON object on stdin and writes one DecisionResult. The tool payload is under `payload`. Exit 0 when a judgment was produced, including `review` and `escalate`. Exit 1 when it was not; stdout still carries `error.code`. Exit 2 is a bad invocation.
- DecisionResult fields: `schema_version`, `policy_version`, `server_version`, `tool`, `action`, `reason_codes`, `confidence`, `error`, `usage`, `billed_tokens`, `unresolved`, `payload`. No dollar amount. No `decision: true`.
- `gate --diff <git-range> --claims <file> --tests <file>` reads those locally. It refuses a path outside the repo, a symlink that resolves outside the repo, and the key file. It does not branch on a harness.
- The completion hook is a separate opt-in fragment. The program matches `git push`, `gh pr create`, and `gh pr merge`. Claude Code's fragment matcher is the tool name `Bash`, because PreToolUse matches tool names; any other Bash command exits 0 with no stdout. It is not `hook gate` and not `docs/harness/gate.hooks.json`. `install` does not enable it. A missing credential fails open: empty stdout, a typed error that is not an allow.
- Adapters exist only for Claude Code and Codex. Other harness hook protocols are unverified and are not implemented.

## Consequences

- A paid call happens only when an operator turns the completion hook on, or runs `gate`.
- The core package has no harness name in its branch.
