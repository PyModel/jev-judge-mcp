# JevBench public subset: `jev_classify` accuracy, cost, and policy routing (2026-09-26)

A paid run of the classify-compatible subset of JevBench's public items through this server's
`jev_classify` on the pinned model, one tool call per item. It fills the gap the earlier studies
left open: when Jev answers a fixed-choice question, how often is it right, what does a decision
cost, and what does the auto/review policy do with the answers it is unsure about.

The machine-checkable evidence is tracked beside this report: the scored summary
[`jevbench-public-score.json`](jevbench-public-score.json) and the 92 recorded outputs
[`jevbench-public-outputs.jsonl`](jevbench-public-outputs.jsonl). The dataset and manifest are
build artifacts, regenerated offline from the pinned JevBench checkout by the adapter, and are
not committed. Every figure below is derived from those records.

History: `bench150.md` measured latency and agent wall time (accuracy unscored); `agent-outcomes.md`
measured agents with and without the Jev MCP server. Neither measured Jev's decision quality; this
run does.

## Method

- **Date:** 2026-09-26.
- **Model:** `jev-1.13.0`, pinned (TypeSafe provider); the guarded runner aborts on any other
  reported model.
- **Tool:** `jev_classify`, one call per item, at most one provider request per call (`NO_RETRIES`),
  through the repo's guarded live runner with an explicit 92-call cap.
- **Items:** the 92 of JevBench's 231 public items (`fstandhartinger/jevbench`, snapshot
  `1bcc55eb…`, sha256-pinned per file) that are classify-compatible: a `choice` question whose state
  is string item text within the classify item cap. The adapter `evals/external/jevbench.py`
  reads the checkout as data and never executes its code; state becomes item text, labels +
  criteria become the class catalog, instructions become the purpose, `expected` becomes gold.
  Items are excluded, never distorted. Per tier: easy 36/48, original 36/72, hard 20/111.
  Exclusions: noul/score questions (no classify mapping), structured states, over-cap states.
- **Scorer:** `evals.runners.score`, offline, split `all`. Gold labels come from the converted
  dataset; actions and decisions are read from the recorded tool results, never recomputed.

## Not a leaderboard number

This is the classify-compatible subset, not JevBench: 139 of 231 public items are excluded (91 of
111 hard-tier items among them), and this run packs one item per call rather than JevBench's own
prompt packing. Any accuracy here is not comparable to JevBench's published 231-item leaderboard
rows and must not be reported as one. The like-for-like comparison is item-aligned (next section).

## Results

92 calls, 0 tool errors, every answer from the pinned model. **89/92 items correct (96.7%)** —
easy 36/36, original 36/36, hard 17/20. The policy auto-accepted 86 of the 92 answers and every
one of the 86 was correct (86/86); the 6 answers it routed to review hold all 3 misses, so no
wrong answer was auto-accepted.

| tier | items | correct | auto | review |
|---|---|---|---|---|
| easy | 36 | 36/36 | 36 | 0 |
| original | 36 | 36/36 | 36 | 0 |
| hard | 20 | 17/20 | 14 | 6 |
| all | 92 | 89/92 | 86 | 6 |

### Per family

Item ids are `<tier>-<family>-<index>`; original-tier families come in six labeled variants
(`-01` … `-06`), two items each, all 2/2 and all auto.

| tier | family | items | correct | review-routed |
|---|---|---|---|---|
| easy | intent | 12 | 12/12 | 0 |
| easy | extraction | 12 | 12/12 | 0 |
| easy | tool_selection | 12 | 12/12 | 0 |
| original | intent-01…06 | 12 | 12/12 | 0 |
| original | routing-01…06 | 12 | 12/12 | 0 |
| original | extraction-01…06 | 12 | 12/12 | 0 |
| hard | opus-b-ambiguous | 6 | 4/6 | 2 |
| hard | opus-b-probability | 2 | 2/2 | 1 |
| hard | opus-b-tradeoff | 3 | 3/3 | 2 |
| hard | opus-c-probability | 1 | 1/1 | 0 |
| hard | opus-c-temporal_numeric | 1 | 0/1 | 1 |
| hard | sol-a-adversarial | 3 | 3/3 | 0 |
| hard | sol-a-trap | 3 | 3/3 | 0 |
| hard | sol-c-multi_hop | 1 | 1/1 | 0 |

### Policy routing

| decision | items | correct |
|---|---|---|
| auto | 86 | 86/86 |
| review | 6 | 3/6 |

Review-routed items (confidence as returned):

| item | returned | confidence | outcome |
|---|---|---|---|
| `hard-hard-opus-b-ambiguous-03` | `within_limitation` | 0.74 | miss (misclassified) |
| `hard-hard-opus-b-ambiguous-09` | `compromised` | 0.53 | miss (misclassified) |
| `hard-hard-opus-b-probability-02` | `upstream_provider` | 0.24 | correct, held for review |
| `hard-hard-opus-b-tradeoff-03` | `order_b` | 0.50 | correct, held for review |
| `hard-hard-opus-b-tradeoff-07` | `p0_fix_24h` | 0.31 | correct, held for review |
| `hard-hard-opus-c-temporal_numeric-04` | invalid response (no classification) | — | miss (no answer) |

The two answered misses sat below the policy's `auto_accept` bar (0.85 with margin 0.5 on this
run); the third failed closed with no answer at all. The policy's auto/review split — not raw
accuracy — is what a caller consumes: every auto-accepted answer was right, and every wrong answer
went to a second check instead of through.

### Scorer metrics

`evals.runners.score`, split `all`, primary `selective_accuracy_auto`:

| metric | value |
|---|---|
| `selective_accuracy_auto` | 1.0 |
| `auto_coverage` | 0.935 (86/92) |
| `micro_f1` | 0.9727 |
| `macro_f1` | 0.9254 |

### Cost and tokens

| measure | value |
|---|---|
| calls | 92 |
| billed input tokens | 54,308 |
| output tokens | 5,156 |
| spend | $0.002281 |
| per 1,000 decisions | ≈ $0.025 |

Spend is input tokens only, at $0.042 per M input tokens with output not billed:
54,308 × $0.042/M = $0.002281 — about $0.025 per 1,000 decisions at this packing. One provider
request per call, no retries, so cost equals raw SDK access at this packing.

## Item-aligned reference comparison

JevBench publishes per-task outcomes for public items in its own repository:
`results/v1.2/jevbench-v1.2-per-task.json` at commit `1bcc55eb`, under
`systems['jev-1.13.0'].public_tasks` — the same system key as the v1.2 leaderboard row, display
name "Jev 1.13.0 (TypeSafe AI)"; the file's own note says per-task outcomes are published for
public items only. Read for the same 92 ids, that record scores 89/92: 89 `c` (correct) and 3 `w`
(wrong) — outcome codes, not raw predictions.

Caveats: it is a v1.2-era measurement of the same items under JevBench's own caller framing, not
this run's prompts, thresholds, or policy, and only the accuracy count is like-for-like — the
record carries one confidence per item (0.603–0.779 across these 92), never this server's actions,
so its confidences are not policy-comparable.

Misses: this run `hard-hard-opus-b-ambiguous-03`, `hard-hard-opus-b-ambiguous-09`,
`hard-hard-opus-c-temporal_numeric-04`; the reference `hard-hard-opus-b-ambiguous-03`,
`hard-hard-opus-b-probability-02`, `hard-hard-opus-c-temporal_numeric-04`. Shared: `ambiguous-03`
and `temporal_numeric-04`; this run alone: `ambiguous-09`; the reference alone: `probability-02`.
Same total, one item apart.

## What this run supports

- Jev's own decision quality on fixed-choice questions through this server: 89/92, with every
  auto-accepted answer correct and every miss routed to review.
- Decision cost at this packing: ≈ $0.025 per 1,000 decisions.
- Nothing else. It does not measure latency (that is `bench150.md`: median 464.6 ms round trip over
  157 calls), any tool other than `jev_classify`, or agents — for agents, `bench150.md` and the
  2026-09-23 `agent-outcomes.md` study measured the same or slower wall time with Jev and no
  accuracy gain.

Descriptive only: one run, one repeat per item, no significance test.
