# evals — L3 judgment quality and L4 agent utility

The layers, per-tool metrics, calibration rule, and precision targets are defined in
[ROADMAP.md § P7](../docs/ROADMAP.md#p7--eval-framework-py-11-py-12-py-13). This directory implements them; it does
not restate them. L1 (software correctness) is the test suite and L2 (TS↔Py parity) is `tests/parity/`;
neither lives here.

## What runs where

| Command | Network | What it does |
|---|---|---|
| `make eval` | none | Offline tests of every scorer, the split, the bounds, threshold selection, and flip rates (`tests/evals/`), on synthetic data |
| `make eval-live` | **live, paid** | `tests/evals/test_live_typesafe.py` (marker `live`): runs the `live-*` manifests through TypeSafe and scores the result. Fails, not skips, without `TYPESAFE_API_KEY` |
| `python -m evals.runners.score MANIFEST OUTPUTS [--split S] [--report PATH]` | none | Scores recorded tool outputs against a dataset's gold labels |
| `JEV_EVAL_LIVE=1 python -m evals.runners.live MANIFEST OUT [--repeats N]` | **live, paid** | Calls the tools through the configured provider and records outputs |
| `JEV_EVAL_LIVE=1 python -m evals.calibration.order` | **live, paid** | Order-sensitivity probe (A2): one jev_verify batch sent forward and reversed, per-claim verdict stability reported; exactly 2 requests |
| `JEV_AB_LIVE=1 make ab AGENT=claude\|pi` | **live, paid** | L4 agent outcome study (below); writes `reports/agent-outcomes.md`. `python -m evals.ab.run --report-only` re-renders it offline |
| `JEV_BENCH_LIVE=1 python -m evals.bench.run` | **live, paid** | The 150-question before/after bench (below). Not wired to any make target; refuses while any item label is not `frozen` |

The live runner refuses to start unless `JEV_EVAL_LIVE=1` is in its environment, when the server's
resolved model is not the manifest's pinned model (`jev-latest` is never a pin), and when cases ×
repeats exceeds the hard cap. A result reporting a different model aborts the run. No make target or CI
job sets the flag (`tests/evals/test_runners.py` checks); the live test passes it to the runner directly.
The flag is eval-only and stays out of `jev_judge_mcp.settings`.

Live bounds:

- **Pinned model:** `jev-1.13.0` (TypeSafe, `JEV_PROVIDER=typesafe`), in `manifests/live-*.json`.
- **Request cap:** `LIVE_REQUEST_CAP = 25` tool calls per run (`runners/live.py`); each tool call makes at
  most one provider request, checked before the first request is sent. The runtime's provider passes
  `NO_RETRIES` (the server's default stays the bounded ADR-0057 policy), so a failed call is a recorded
  `error` row, never an unbudgeted retry.
- **Datasets:** `datasets/synthetic/live-classify.jsonl` and `live-verify.jsonl`, 3 cases each, so
  `make eval-live` sends 6 requests, plus the order-sensitivity probe's 2 (`test_live_order.py`).
- Neither `make eval` nor `make ci` calls the network; both stay green with the key unset.

## Layout

| Path | Contents |
|---|---|
| `datasets/` | JSONL cases `{"id", "family", "input", "gold"}`; `input` is the tool arguments. Small `synthetic/` sets, plus `bench150/items.jsonl` (the bench's 150 item texts, labels still `draft`) |
| `manifests/` | One JSON per run: `tool`, `dataset` (relative to `datasets/`), pinned `model`, split `salt`, scorer `params` |
| `scorers/` | `metrics.py` (generic math), `tools.py` (one scorer per tool), `fields.py` (tolerant JSON readers) |
| `calibration/` | `split.py` (60/20/20 by family), `rows.py` (rows from `scorers/tools.py` `judgments`), `flips.py`. `bounds.py`, `threshold.py`, `targets.py` moved into `jev_judge_mcp.calibration` (ADR-0069) so the installed `calibrate` command shares them |
| `runners/` | `score.py` (offline), `live.py` (guarded), `manifest.py` (file formats) |
| `baselines/` | `ranking.py`: original order and BM25. The embeddings baseline raises `NotImplementedError`: it needs a pinned embedding model |
| `reports/` | Per-run directories and working reports (gitignored). Finished reports — `bench150.*`, `agent-outcomes.md`, `p8-pilot.md` — are committed as records via `.gitignore` exceptions |

Recorded outputs are JSONL `{"id", "repeat", "output"}`, where `output` is the parsed tool result; a tool
error is recorded as `{"id", "repeat", "error"}` and scores as a wrong answer.

## Metric → scorer map

Primary metric first. All scorers are in `scorers/tools.py`; gold shapes are in each scorer's docstring.

| Tool | Scorer | Metrics reported | Gold |
|---|---|---|---|
| verify | `score_verify` | `contradiction_recall`, `macro_f1`, `brier`, `ece`, `selective_accuracy`, `auto_coverage` | `claims: {id: verdict}` |
| screen | `score_screen` | `injection_recall_at_false_block_rate` (+ `operating_threshold`, `false_block_rate`), `pr_auc`, `skip_precision` | `injection`, `skip` |
| find | `score_find` | `recall_at_1`, `exists_auroc`, `mrr`, `ndcg_at_10`, `verdict_accuracy` | `relevance: {id: grade}`, `verdict?` |
| rerank | `score_rerank` | `ndcg_at_10`, `mrr`, `kendall_tau`, and the same for original order / BM25 | `relevance: {id: grade}` |
| classify | `score_classify` | `selective_accuracy_auto`, `auto_coverage`, `macro_f1`, `micro_f1` | `labels: {id: class}` |
| decide | `score_decide` | `overdecision_rate`, `escape_hatch_accuracy`, `requirement_check_f1` | `acceptable: [id]` (empty = should escape), `checks?` |
| compare | `score_compare` | `perturbed_contradiction_recall`, `aspect_macro_f1` | `relation`, `perturbation?`, `aspects?` |
| extract | `score_extract` | `exact_match`, `hallucinated_values` (values absent from the document; a count, must be 0), `not_found_precision`, `not_found_recall` | `fields: {id: value \| null}` |
| review | `score_review` | `p_defective_given_auto`, `safe_to_apply_auroc`, `severe_defect_recall` | `defective`, `severe?` |
| gate | `score_gate` | `false_auto_rate` (+ `false_auto_upper_bound`, one-sided 95% Clopper-Pearson), `claim_macro_f1`, `reason_code_accuracy` | `safe`, `claims?`, `reason_codes?` |
| score | `score_jev_score` | `nearest_level_accuracy`, `level_mae`, `within_one_rate`, `gold_level_probability` | `level: int` (0-based; extension tool, ADR-0048 — no bench150 corpus yet) |

Actions and decisions are read from the tool result, never recomputed, so the eval grades the server's
policy rather than a copy of it. An undefined metric (no positives, no AUTO rows) is `null`, not 0.

## Calibration

`score --split calibration` on a tool in `calibration/rows.py` adds a `calibration` block: the threshold
with the most AUTO rows whose upper error bound (Clopper-Pearson, one-sided 95%) is at most
`1 - target` from `jev_judge_mcp.calibration.targets`, or `null` when none qualifies. Rows within ±0.05 of that
threshold (inclusive) are borderline; with 3–5 recorded repeats their flip rate is reported. The block
then certifies the selected point on the `locked_test` split (`jev_judge_mcp.calibration.threshold` `certify`):
that split's own AUTO rows and errors, its one-sided 95% Clopper-Pearson upper bound, and a `gate` of
`pass` only when that held-out bound is within the budget. A point that fits the budget on calibration
but not on `locked_test` fails the gate: the calibration rows chose the point, so they cannot certify
it. An empty or evidence-free `locked_test` split bounds at 1.0 and fails. The operating point is a
report. It never edits `jev_judge_mcp.policy.thresholds`: moving a frozen default is a
Sanctioned Divergence and needs its own ADR.

## Open gaps (not decided by the ROADMAP or an ADR)

- **Held-out certification needs `locked_test` rows.** `score --split calibration` requires a recorded
  output for every `locked_test` case (an error, not a skip, per `runners/manifest.py`), and the
  Clopper-Pearson bound cannot fall under a realistic budget without enough of them: zero errors in 20
  rows already bound at ~14%, so a 3% budget needs ≳100 error-free rows per tool (jev_gate's 0.5%
  budget, ≳600). Until a per-tool `locked_test` corpus of that size is recorded, a failing gate is the
  expected state, not a defect.

- **Screen's fixed false-block rate** has no specified value; `score_screen` requires
  `params.max_false_block_rate` in the manifest rather than picking one.
- **Joint thresholds**: calibration sweeps one score per tool (`rows.py` names it). Classify, compare,
  and extract also gate on `minimum_margin`, and review on its composite floor; those are not calibrated
  jointly. jev_gate combines review and claim thresholds, so it has no single-score calibration; its
  report carries the false-AUTO rate and its upper bound.
- **Embeddings baseline** for rerank needs a pinned embedding model and is not implemented.
- **L4 outcome study, live:** run 2026-09-23 for both agents (Claude Code `claude-sonnet-5`, Pi `ds4/glm-5.3-flash`); 17 of 18 pairs measured, one excluded because its with-Jev run never called Jev. The rendered report and the minimal raw records needed to re-render it live in [`docs/evals/`](../docs/evals/README.md); `python -m evals.ab.run --report-only` re-renders from recorded runs. Three tasks x 3 repeats is small: its report is descriptive, with no significance test.

## L4 agent outcome study (`ab/`, ROADMAP P8, ADR-0030)

Separate from L3: it asks whether Jev improves a coding agent **while the agent uses the Jev MCP tools**,
not whether Jev's answers are right. One agent at a time (Claude Code or Pi, `AGENT=`), two arms: **A**
without Jev, **B** the same agent with this worktree's Python server as MCP server `jev` behind the recording
proxy (`JEV_PROVIDER=typesafe`, `JEV_MCP_MODEL=jev-1.13.0`). The arms share the model, effort or thinking,
temperature, built-in tools, harness server, user prompt, limits, timeout, snapshot and grader; B's system
addendum adds one sentence naming the task's Jev tool. `tests/evals/test_ab_harness.py` asserts that is the
only difference in the command line and that the MCP configs differ only in `jev`.

Each task hinges on a judgment a Jev tool is for, and ships a gold decision the snapshot's documents settle:

| Task | Judgment | Jev tool | Gold |
|---|---|---|---|
| `j1-refund-window` | boundary: which refund window the policy sets (the stale FAQ disagrees) | `jev_verify` | delivery date, day 30 inclusive |
| `j2-ticket-route` | classification: which queue a mixed billing and unrecognized sign-in ticket belongs in | `jev_classify` | `security` |
| `j3-installment-patch` | choice among three plausible patches for an installment split | `jev_decide` | `patch-c` |

A pair (agent, task, repeat) is evidence only when both arms are measured: the run reached the model
(some frontier call produced output), and in B the proxy log holds a Jev answer from the pinned model. A
with-Jev run where the agent never called Jev is a failed measurement, and its whole pair is dropped; the
report counts it under "Excluded pairs". A run that reached the model and then failed (a wrong fix, a
timeout) is a measured failure. Success is the grader: every hidden acceptance test passes, no pre-existing
test regresses, no pre-existing test file changes. The report (`ab/report.py`) gives, per agent and arm over
measured pairs only: tasks solved, time to a correct solution (median, n, min, max, and the values while
n ≤ 10), correct solutions per hour (failed runs' time included), final tests passed, wrong branches, test
cycles and retries, tool calls and tokens per solved task, Jev calls, judge accuracy against gold, and paired
counts and wall-time differences. With no measured pair it says "not measured" and prints no number and no
difference. It never reports an item rate. Every run also records its cost, unsafe actions, and each Jev
call's round trip.

| Path | Role |
|---|---|
| `ab/arms.py` | The agent configuration both arms share (Claude CLI flags, model, budget), arm B's Jev sentence, and each arm's MCP config |
| `ab/fixture/` | `snapshot/` (the throwaway repo with its policy docs, copied per run to a temp dir outside this repo) and `tasks/<id>/` (prompt, overlay, hidden acceptance tests, reference solution, one solution per wrong option under `distractors/`, and `task.json`: options, gold, Jev tool, wrong-branch signatures). Excluded from pyright: its imports resolve only inside a copy |
| `ab/grade.py` | Pristine pre-existing tests + acceptance tests against the agent's tree: success, regressions, pass rate, protected-file changes |
| `ab/outcomes.py` | Per-run measures and their definitions: reached the model, Jev called and used, test cycles, retries, wrong branches, decision, measurement |
| `ab/proxy.py` | Recorder on `evals/relay.py`: per `tools/call` round-trip ms, isError, `usage.input_tokens`, action, headline Action, model. Never logs arguments, text, or error messages |
| `ab/review_server.py` | `request_human_review`, in both arms |
| `ab/unsafe.py`, `ab/stream.py` | Unsafe-action rules over tool calls; Claude stream-json to frontier calls, context and output tokens (Pi's parser is `bench/pi.py`) |
| `ab/ledger.py` | One `SpendPolicy` per agent: 18 runs (3 tasks x 2 arms x 3 repeats), 25 USD, checked per pair. Pi's worst case is its Jev headroom only |
| `ab/run.py`, `ab/report.py` | Seeded pair schedule, the runner and its refusals, the pinned-setup guard, and the markdown report |

The study refuses without `JEV_AB_LIVE=1` (no make target or CI job sets it), without `TYPESAFE_API_KEY`,
without the agent binary (and, for Pi, the MCP adapter), and when `reports/agent-outcomes/<agent>/meta.json`
records a different Jev revision, fixture hash, agent version, held-constant setup, or hardware: pairs
never span two setups. Every run goes through `evals/agent.py`'s `run_agent`: a fresh sandbox, a minimal env
with no provider keys, the key only in the Jev server's env through a 0600 config file, a timeout, cleanup,
and secret scrubbing. Raw runs land in `reports/agent-outcomes/<agent>/` (gitignored); the report in
`reports/agent-outcomes.md` is tracked once it holds live data, and the durable record — rendered reports
plus the minimal raw records needed to re-render offline — lives in
[`docs/evals/`](../docs/evals/README.md). A report from stub data is never committed. Offline tests:
`tests/evals/test_ab_harness.py` (fixture, arms, measures, report on synthetic records),
`tests/evals/test_agent.py` (the runner with stand-in agents), and `tests/evals/test_ab_dryrun.py` (the
study end to end: a stub agent through the proxy to the real server on a loopback provider). All run under
`make eval`.

**History.** `reports/p8-pilot.md` is the earlier three-arm pilot (2026-09-21): its tasks needed no
judgment, no run called a Jev tool, and every arm solved every task, so it could not tell the arms apart. Its
tasks and runner were replaced by the study above; the report is kept as recorded, with a one-line history
banner added at the top.

## 150-question bench (`bench/`)

An L4 study on judgment questions with three arms on the same items (ADR-0036). Arm **A** is direct:
no Jev server. Arm **B** is automatic: the Jev server is available and the prompt does not mention it.
A B run that never calls Jev is recorded as "did not call Jev" and is not a failure. Arm **C** is
forced: the server plus one sentence telling the agent to call Jev; a C run with no Jev answer fails
the use gate. Reuses `ab/arms.py`, `ab/stream.py`, `evals/agent.py` and `evals/relay.py`. Items:
`datasets/bench150/items.jsonl`, 15 per tool in 3 families of 5; every row is `draft` with no gold, no
`accept`, and no labelers. `intended` is the author's target for the class mix, drafted by an agent and
unverified, not a label: labelers label blind to it. Gold comes from two human labelers and an
adjudicator before any accuracy claim. For find, label `verdict` as `answered` or `absent` only: the
agent's answer wraps to one of those two, so a `partial` gold would cap `verdict_accuracy` by
construction. Accuracy on the current run is not measured (0 labeled items).
The [human labeling packet](datasets/bench150/labeling/README.md) contains the proposed rubric,
150 blinded questions, separate reviewer sheets and human assignments. It supplies no gold.
`reports/bench150-sample.html` is the results view the dry-run code path writes from the stub
triplets, committed as a sample; it is not a bench result.

| Path | Role |
|---|---|
| `bench/items.py` | Row schema and loader (`load_cases`-compatible superset); draft rows may not carry gold |
| `bench/prompt.py` | Shared bench addendum, C's one-sentence Jev instruction, per-tool prompt renderer |
| `bench/answer.py` | Final-line `{"answer": ...}` parser, `accept` scoring, wrapper into each scorer's result shape |
| `bench/gate.py` | Use gate (a C run needs a successful Jev answer from `jev-1.13.0`; a B run that never calls Jev is "did not call Jev", not a failure), proxy-vs-stream cross-check (an `unanswered` row matches a stream error or a missing result) |
| `bench/proxy.py` | Bench recorder on the relay: seq, epoch timestamps, arguments and result text (or a JSON-RPC error's message); refuses a run's calls past its own `BENCH_REQUEST_CAP` (25, inside `JEV_RUN_BOUND_USD`; not `make eval-live`'s cap), and every id-less or batched `tools/call` |
| `bench/spans.py` | Server DEBUG spans to per-call S2-S5 timings, by order only; overlapping calls are unattributed |
| `bench/stats.py`, `bench/analysis.py` | Exact McNemar and sign tests on `jev_judge_mcp.calibration.bounds`' binomial; complete pairs, early stops; `per_tool`, each tool's own scorer over the wrapped answers of labeled items (in `summary.json`). Screen's primary metric is structurally null there: agent answers carry no injection probability |
| `bench/ledger.py`, `bench/run.py` | The bench's own `SpendPolicy`: 450 runs / 25 USD of Jev spend, checked per triplet, spent independently of the pilot's; seeded triplet schedule; one-prompt Pi preflight; a provider connection, auth, or rate-limit error stops the run and is not an arm result; the runner and its refusals |
| `bench/chart.py` | The results view: one self-contained HTML file, inline SVG bars (accuracy, items/min, total wall time) for A, B, and C |

Tests: `tests/evals/test_agent.py` (the shared runner, and the pilot and bench driving it with stand-in
agents), `tests/evals/test_bench.py` (units) and `tests/evals/test_bench_dryrun.py`, which drives the runner
end to end with a stub agent (`tests/support/bench_agent.py`) and a loopback provider
(`tests/support/bench_provider.py`) over the two-row `tests/evals/data/bench-dryrun.jsonl`, and writes a
sample results view. `tests/evals/data/claude-stream-json-sample.jsonl` is a recorded P8 stream (home path
scrubbed) that pins `tool_result` parsing. All of it runs under `make eval`, offline.

## bench150 record (history)

The current result is the three-arm run in `reports/bench150.md` (Pi `opencode-go/deepseek-v4.1-flash`, thinking high). The paragraph below is the earlier two-arm run, kept as history, not a result about agents. Pi `ds4/glm-5.3-flash`,
without against with Jev, on the 150 judgment questions. Accuracy was not measured: 0 labeled items, no
gold invented. 107 of 150 with-Jev runs never reached the local model (connection error). On the 43 pairs
where both arms ran, the recorded item rate (3.38 against 0.79 items per minute) and total time (762.71 s
against 3266.66 s) measure evaluator overhead: a question answered with and without an extra tool call.
They say nothing about whether Jev makes an agent better at a task. Chart and table:
[`reports/bench150.html`](reports/bench150.html), [`reports/bench150.md`](reports/bench150.md).
