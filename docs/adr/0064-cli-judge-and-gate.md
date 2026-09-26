---
status: accepted
---
# The CLI is the harness-agnostic path

MCP stays the product surface for clients that speak it. A client that cannot is not a reason to fork policy. `jev-judge-mcp judge` and `jev-judge-mcp gate` call the same tools.

## Decision

- `judge <tool>` reads one JSON object on stdin and writes one DecisionResult. The tool payload is under `payload`. Exit 0 when a judgment was produced, including `review` and `escalate`. Exit 1 when it was not; stdout still carries `error.code`. Exit 2 is a bad invocation.
- DecisionResult fields: `schema_version`, `policy_version`, `server_version`, `tool`, `action`, `reason_codes`, `confidence`, `error`, `usage`, `billed_tokens`, `unresolved`, `payload`. No dollar amount. No `decision: true`.
- `gate --diff <git-range> --claims <file> --tests <file>` reads those locally. It refuses a path outside the repo, a symlink that resolves outside the repo, and the key file. It does not branch on a harness.
- The completion hook is a separate opt-in fragment. The program matches `git push`, `gh pr create`, and `gh pr merge`. Claude Code's fragment matcher is the tool name `Bash`, because PreToolUse matches tool names; any other Bash command exits 0 with no stdout. It is not `hook gate` and not `docs/harness/gate.hooks.json`. `install` does not enable it. A missing credential fails open: empty stdout, a typed error that is not an allow, unless `JEV_HOOK_REQUIRED=1` (ADR-0065), which asks. The command is the executable path. A runner flag that changes the working directory makes `gate` judge the wrong repo.
- Adapters exist only for Claude Code and Codex. Other harness hook protocols are unverified and are not implemented.

## Consequences

- A paid call happens only when an operator turns the completion hook on, or runs `gate`.
- The core package has no harness name in its branch.

## Amendment (2026-09-25): the envelope reads each tool's own decision field

`judge` first read only a top-level `action`, which only `jev_gate` and `jev_review` set, so
every other tool's envelope carried `action: null` and `unresolved: true` even on a clean call
(a passing screen, an all-auto verify). The envelope now maps each tool's own decision field,
in one registry (`cli._DECISIONS`, guarded by `tests/unit/test_cli_judge.py`):

- `jev_gate`, `jev_review`: their top-level `action` — unchanged behavior.
- `jev_screen`: `recommendation.action`; resolved only on `pass`.
- `jev_verify`, `jev_classify`, `jev_extract`: the worst per-row `action` / `decision` / `status`
  (an invalid or broken row counts as `review`; extract's `not_found` is neutral); resolved only
  on `auto`.
- `jev_compare`: `overall.decision` (aspects are not the headline); resolved only on `auto`.
- `jev_decide`: `recommendation`; unresolved when no candidate was selected or an escape hatch
  won; the envelope `action` stays null.
- `jev_find`, `jev_rerank`, `jev_score`: no action vocabulary; `action` stays null, unresolved
  on `invalid_response` (jev_score: anything but `ok`).

`unresolved` still means "not a green light", never "failed": a `block` or `skip` screen, an
escaped decide, and a `review` row are decided answers that need the caller's attention, and a
failed call still writes `error` with exit non-zero, as before. Exit codes are unchanged.
