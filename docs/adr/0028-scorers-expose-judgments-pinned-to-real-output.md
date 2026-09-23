---
status: accepted
---
# Scorers expose per-tool judgments and are pinned to real server output

`evals/scorers/tools.py` is deep at its seam: `SCORERS[tool](examples, params) -> ToolScore`, with
ten adapters. Scorers read Actions from tool output and never recompute Policy. The review found
three places where knowledge of result shapes leaks around that seam:
- `evals/calibration/rows.py` re-reads the same result fields and joins on gold ids a second time,
  for verify, classify and extract.
- Every scorer test runs on hand-written dicts. A probe through the real `Toolset` confirmed the
  shapes match today (permissive answers give `invalid = 0` for all ten tools), but nothing pins
  that.
- `evals/bench/answer.py` `wrap` has no production caller. It writes `status: "found"`, which the
  server never emits, and gives screen no probabilities, so screen's primary metric is always null.

Separately, `evals/ab/report.py`'s "review/escalate verdicts" count reads a top-level `action`
field. Only review and gate have one, so the count silently leaves out verify, classify, compare,
extract and screen.

## Decision

- **Judgments.** `scorers/tools.py` gains `judgments(tool, examples) -> list[Judgment(key, gold,
  predicted, score, auto)]`, built from the same adapters, and `ToolScore` is derived from it.
- **Calibration.** Calibration rows come from judgments,
  `[(j.score, j.gold == j.predicted) for j in judgments if j.score is not None]`, so there is one
  join per tool.
- **Contract test.** `tests/evals/test_real_output.py` runs every tool through the real `Toolset`
  with the fake Provider and the `tests/security/tools.py` answer triad (permissive, hostile,
  empty). It asserts:
  - permissive answers give `invalid == 0` and every `predicted` is non-null;
  - empty answers give exactly the documented invalid counts;
  - a calibration row exists whenever the result carries a decision.
- **`wrap`.** It gets its caller: a per-tool breakdown in `bench/analysis.py`, with `found`
  corrected to `auto`. Screen's primary metric is documented as structurally null for agent
  answers.
- **The P8 verdict count** reads each tool's headline Action as defined in ADR-0013's 2026-09-22
  amendment, instead of a top-level `action` key.
- **Hand-written scorer dicts stay** for edge-case metrics.

## Consequences

- A payload change that would silently break a scorer fails an eval test, not only the parity
  replay.
- The committed P8 report numbers are records of a finished run and are not re-rendered by this
  change.
