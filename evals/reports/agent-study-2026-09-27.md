# Agent study, 2026-09-27: Jev latency and with/without-Jev agent outcomes

The measured tables from one owner-run eval on 2026-09-27, copied from its report so that
[`docs/research/jev-best-use.md`](../../docs/research/jev-best-use.md) can cite tracked numbers.
The raw run records were not kept, so these tables are the only copy. Nothing here was re-run.

**Common setup.**
- jev-judge-mcp at `11f482a` (the 0.5.0 release commit), plus uncommitted scratch pins in the eval
  harness only: `evals/bench/pi.py` `PI_MODEL` → `opencode-go/deepseek-v4.1-flash`; `evals/ab/arms.py`
  `AGENT_MODEL` → `claude-haiku-4-5-20251001`, left over from a stopped Haiku arm and not used by the
  runs below; `evals/agent.py` `_isolated_env` changed to fix Claude login. `src/` was unchanged.
- **Agent:** Pi 0.87.1 on `opencode-go/deepseek-v4.1-flash`, thinking `high`.
- **Jev model:** `jev-1.13.0` was pinned for the JevBench run. The L4 and bench150 sections below do
  not record which Jev model they used.

## JevBench, classify-compatible subset (92 items)

JevBench commit `1bcc55eb6c8cffde2306b3db03ede39b61c6152a`. 92 items were kept and 139 excluded
(easy 36/48, original 36/72, hard 20/111). Run with `--cap 92`, sequential, no retries. Per-call
latency comes from a wrapper that timed the runner's own `collect` loop with `perf_counter`.

| metric | value |
|---|---|
| item accuracy | 88/92 = 95.7% |
| — easy | 36/36 |
| — original | 36/36 |
| — hard | 16/20 |
| auto coverage | 86/92 = 93.5% |
| selective_accuracy_auto | 85/86 = 0.988 |
| macro_f1 / micro_f1 | 0.884 / 0.957 |
| errors | 0/92 |
| latency per call | p50 203 ms, p95 360 ms, max 451 ms, mean 219 ms; total wall 20.1 s |
| tokens | 54,308 input total, ~590/call (output not billed) |
| cost | ~$0.003 at list price |

One of the four wrong items was routed `auto`: `hard-opus-b-ambiguous-03`. The other three were
routed `review`. This is the classify-compatible subset only, not JevBench, and must not be compared
with JevBench's leaderboard.

## L4 outcome study: with vs without Jev (3 tasks × 3 repeats)

**Setup.**
- 18 runs, 9 pairs, all 9 measured, 0 excluded. Timeout 900 s.
- Fixture sha256 `0c7d6b…`. Jev revision `11f482a…+dirty`.
- Arm B differs from arm A only by the Jev integration: the server plus one addendum sentence.

| measure | A: without Jev | B: with Jev MCP |
|---|---|---|
| tasks solved | 9/9 | 5/9 |
| time to a correct solution, s (solved runs) | median 22.6 (n=9, 15.1–32.6) | median 38.9 (n=5, 29.4–42.4) |
| all run wall time, s | median 22.6 (n=9, 15.1–32.6), total 211.2 | median 38.9 (n=9, 28.5–70.3), total 367.1 |
| correct solutions per hour | 153.38 | 49.03 |
| final tests passed | 9/9 | 9/9 |
| hidden acceptance tests | 39/39 | 39/39 |
| regressions / protected files changed | 0 / 0 | 0 / 4 runs |
| wrong branches (runs with one / runs on tasks with signatures; total) | 0/6; 0 | 1/6; 1 |
| test cycles per run | median 1.0 (12 total) | median 1.0 (13 total) |
| retries (total) | 3 | 4 |
| tool calls (total; per solved task) | 99; 11.0 | 156; 31.2 |
| tokens per solved task (context + output) | 36,328 | 140,032 |
| tokens, all runs (context + output) | 326,948 (307,381 + 19,567) | 700,161 (664,419 + 35,742) |
| Jev calls (total; runs with one) | 0; 0/9 | 11; 9/9 |
| judge accuracy (decision = gold) | 9/9 | 9/9 |
| agent model spend | $0.0225 | $0.0388 |
| Jev spend | $0.0000 | $0.0010 |

**Pairs.** Solved by both 5, only without Jev 4, only with Jev 0, neither 0.

**The four with-Jev failures were all `protected_changed`.** In each, the agent had already made the
correct code change, and every hidden acceptance test passed. It then added test methods to a
pre-existing test file, which the grader's protected-file rule fails.

**Jev calls in arm B.**
- j1: one `jev_verify` per run.
- j2: one `jev_classify` per run, plus `jev_gate` in repeats r1 and r3.
- j3: one `jev_decide` per run.

This is descriptive only: n=9 pairs and no significance test.

## bench150 three-arm speed run (150 items)

**Arms.** A is direct (no Jev server). B is automatic (Jev available, the prompt does not mention
it). C is forced (the server, a one-sentence instruction and the use gate); see ADR-0036.

**Run.** 450 runs, all `ok`; all 150 triplets recorded; wall clock 72.7 min.

| arm | items | run wall total | items/min | median wall s | p95 s | Jev calls | Jev spend | agent spend |
|---|---|---|---|---|---|---|---|---|
| A direct | 150 | 1297.4 s (21.6 min) | 6.94 | 4.09 | 7.73 | 0 | $0.0000 | $0.0973 |
| B automatic | 150 | 1095.3 s (18.3 min) | 8.22 | 5.04 | 13.13 | 17 calls in 16/150 runs (10.7%) | $0.0007 | $0.2402 |
| C forced | 150 | 1966.3 s (32.8 min) | 4.58 | 8.66 | 23.30 | 160 calls in 150/150 runs | $0.0062 | $0.3121 |

- **Paired differences vs A:**
  - B − A: median +0.94 s (p90 +4.56, p95 +8.22), sign-test p=1.5e-14.
  - C − A: median +4.38 s (p90 +8.81, p95 +17.05), p=8.6e-37.
- **Use-gate failures in C:** 0/150. This is the bench's gate that a Jev answer came back, not `jev_gate`.
- **Jev round trip** (n=163 spans): p50 324.8 ms, p90 902.9 ms, p95 1178.8 ms.
- **Accuracy:** not measured. 0 items are labelled; all 150 remain `draft`.
- **Records:** the per-run records are not tracked. The tracked `bench150.md` is a different run
  (2026-09-22).
