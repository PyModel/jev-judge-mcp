---
status: accepted
---

# Calibrate is advisory only

Callers judge with the frozen thresholds and may want a threshold fitted to their own traffic. decider ships `python -m decider.calibrate` for exactly that; this repo already owned the stronger statistics (`select_threshold`: maximum AUTO coverage under a Clopper-Pearson upper error bound; `certify`: the same point re-bounded on held-out rows) but they were reachable only through the internal eval harness over recorded datasets.

## Decision

- `jev-judge-mcp calibrate <rows.jsonl> [--tool TOOL] [--max-error P] [--min-rows N]` is an offline subcommand: no provider, no network, no secrets. It reads the caller's labeled rows, one JSON object per line: `{"score", "correct", "family"?, "tool"?}`, where `score` is the scalar the tool compares against `auto_accept` and `correct` is whether the judgment matched gold.
- Rows are validated strictly: every bad line stops the run naming `file:line`; scores are finite numbers in [0, 1]; `correct` is a boolean; unknown fields and unknown tools are rejected. A minimum-rows guard (default 40) and a minimum held-out guard (8 rows) refuse samples too small to bound.
- The rows are split deterministically into selection and held-out (default 30%), whole families never rows, families ordered by `sha256(salt + NUL + family)`. `select_threshold` picks the point on the selection split; `certify` bounds it on the held-out split. The report prints the recommended `auto_accept`, both splits' coverage and errors, and the certified upper error bound, and says when the held-out bound exceeds the budget.
- The error budget comes from `--tool` (the ROADMAP P7 per-tool budgets in `jev_judge_mcp.calibration.targets`) or from `--max-error`; one of them must be present.
- The command never edits a frozen default in `policy/thresholds.py`. Moving a frozen default is a Sanctioned Divergence and needs its own ADR; the help text and the report say so.
- The shared statistics moved from `evals/calibration/` into `jev_judge_mcp.calibration` (`bounds`, `threshold`, `targets`) so the installed package imports the same modules the eval harness does — never a copy. `split`, `rows`, and `flips` stay evals-only.
- Exit codes: 0 a report was produced whose held-out certification fits the budget, 1 no threshold met the budget on the selection rows **or the held-out certification exceeded it**, 2 a bad invocation or bad rows.

## Consequences

- An operator with labeled outcomes gets an honestly bounded threshold recommendation instead of guessing 0.5, without touching the parity-frozen surface: no tool schema, no wire behavior, no policy default changes.
- The eval harness and the shipped command share one statistics implementation; a bound bug fixed for one is fixed for both.
