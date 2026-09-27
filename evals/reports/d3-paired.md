# VOID — the paired comparison recorded here is not evidence

**Void 2026-09-27.** The numbers that were in this file came from a study whose boundary leaked, and
one run read the gold answer. Do not cite them as an agent-outcome or grader result. Do not rewrite
history over them: the records stay as they were recorded.

What disqualifies the study (full evidence in the operator's diagnosis at
`data/jev-eval-d2d3/diagnosis.md`, outside this repository):

1. **Boundary escape.** In most with-Jev runs the agent left the task workspace: it read the
   operator's `~/.pi/agent` configs (including the whole copied `auth.json`), `~/.claude.json`,
   other treehouse worktrees, the primary checkout, and this repo's harness sources, and ran
   filesystem-wide `find`s. Real provider keys reached the model provider as tool output in 7
   transcripts; the operator rotated them.
2. **Gold contamination.** `j9-review-patch.B.r1` read the repo's
   `evals/ab/fixture/tasks/j9-review-patch/task.json`, which carries the gold option.
3. **The with-Jev arm never engaged.** Only 3 of 33 with-Jev runs produced a Jev call, because the
   study's MCP config lacked the bench's eager/`directTools` keys: the agent saw only the adapter's
   gateway. 30 of 33 pairs were therefore excluded, and the with-Jev arm's token cost (~10x the
   without-Jev arm) measured gateway confusion, not Jev.

The grader change this study was meant to exercise (ADR-0071, added tests do not fail a correct
run) is itself pinned by offline tests in `tests/evals/test_ab_harness.py` and does not depend on
this study. A replacement study must wait for the confinement decision (operator, 2026-09-27) and
run no earlier than the boundary work lands.

Raw run records: `evals/reports/d3-paired/pi/` (gitignored, scrubbed by the operator's redactor,
`remaining hits: 0`).
