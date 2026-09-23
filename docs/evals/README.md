# Recorded eval results — 2026-09-23

The full eval, run live on 2026-09-23 through the repo's guarded paths (`evals/README.md`): the L3
judgment-quality run and the L4 agent outcome study for both agents. `evals/reports/` is gitignored, so
this directory holds the rendered reports and the minimal raw recorded outputs needed to re-render them
offline. Nothing here contains a key: the TypeSafe key reaches only the Jev server's env through a 0600
config file and every kept artifact is scrubbed.

- **Pinned Jev model:** `jev-1.13.0` (TypeSafe, `JEV_PROVIDER=typesafe`), every layer.
- **Agent models:** Claude Code 2.1.281, `claude-sonnet-5`, effort medium; Pi 0.87.1, `ds4/glm-5.3-flash`,
  thinking high (local model; only its Jev calls are paid).
- **Revision:** `a9dc147` (the Claude study's meta records the report re-render as `+dirty`; no source
  change). Fixture sha256 `0c7d6b91…`, hardware macOS 27.0, Apple M5 Max, 18 CPUs.
- **Total spend this run:** ≈ $1.50 — L3 $0.0003 (two 6-request runs over the pinned synthetic datasets),
  Pi study $0.0006 of Jev input tokens, Claude study $1.4953 — all inside the $25.00 per-study caps.
  Earlier recorded studies (2026-09-21 pilot, 2026-09-22 Claude study and three-arm bench) spent
  separately and stay as recorded on the tracked reports.

## L3: live judgment quality

`make eval-live` (12 paid requests across two passes: the pytest gate plus the recorded capture), pinned
`jev-1.13.0`, synthetic `live-*` datasets, 3 cases per tool, 1 repeat. Raw outputs and score reports:
[`live/`](live/).

| tool | n | primary | metrics |
|---|---|---|---|
| jev_classify | 6 | selective_accuracy_auto | 1.00 — macro_f1 1.00, micro_f1 1.00, auto_coverage 1.00, invalid 0 |
| jev_verify | 5 | contradiction_recall | 1.00 — macro_f1 1.00, brier 0.00004, ece 0.002, selective_accuracy 1.00, auto_coverage 1.00, invalid 0 |

Every answer came from the pinned model, validated, and scored; no accuracy claim beyond these six
questions. Chart: [`charts/live-l3.svg`](charts/live-l3.svg).

## L4: agent outcome study (ADR-0030)

`JEV_AB_LIVE=1 make ab AGENT=…`: same agent, same tasks, with (B) and without (A) the Jev MCP server; 3
tasks × 2 arms × 3 repeats per agent. A pair is evidence only when both arms are measured; a with-Jev run
that never got a Jev answer from the pinned model drops its whole pair, counted as excluded. No item
rates anywhere: they measure the evaluator, not the agent.

| agent | pairs | measured | excluded | tasks solved A / B | median time to correct A / B | wall diff B−A (both solved) | judge accuracy A / B | spend |
|---|---|---|---|---|---|---|---|---|
| Claude Code (`claude-sonnet-5`) | 9 | 9 | 0 | 6/9 / 6/9 | 14.6 s / 18.3 s | +4.6 s median | 9/9 / 9/9 | $1.4953 |
| Pi (`ds4/glm-5.3-flash`) | 9 | 8 | 1 (with-Jev run never called Jev) | 6/8 / 6/8 | 49.4 s / 127.8 s | +85.8 s median | 8/8 / 8/8 | $0.0006 |

Both agents solved the same pairs with and without Jev (Claude: 6 both, 3 neither; Pi: 6 both, 2
neither over measured pairs), picked the gold decision on every run, and were slower with Jev on every
pair both arms solved. With Jev the agents also used more tokens per solved task (Claude 245.4k vs
139.2k; Pi 93.4k vs 26.6k). This run shows no faster or more accurate agent with Jev. Descriptive only:
n is small and there is no significance test.

- Full rendered report: [`agent-outcomes.md`](agent-outcomes.md) (both agents).
- Charts: [`charts/agent-outcomes-claude.svg`](charts/agent-outcomes-claude.svg),
  [`charts/agent-outcomes-pi.svg`](charts/agent-outcomes-pi.svg),
  [`charts/agent-outcomes-pairs.svg`](charts/agent-outcomes-pairs.svg) — regenerate with
  `uv run python docs/evals/charts.py`.
- Minimal raw records: [`agent-outcomes/<agent>/<run>/result.json`](agent-outcomes/) plus one
  `meta.json` per agent — exactly what the renderer reads.

### Re-render offline

```sh
cp -R docs/evals/agent-outcomes evals/reports/agent-outcomes
uv run python -m evals.ab.run --report-only
```

## History (kept as recorded)

- P8 pilot, 2026-09-21 — no run called a Jev tool: `evals/reports/p8-pilot.md`.
- Claude outcome study, 2026-09-22 — superseded by the 2026-09-23 run above; tracked report history.
- Three-arm bench150, Pi `opencode-go/deepseek-v4.1-flash` — accuracy unmeasured (0 labeled items):
  `evals/reports/bench150.md`, `evals/README.md § bench150 record`.
- The 150-question bench stays blocked until its item labels are frozen by human labelers; nothing here
  changes that.
