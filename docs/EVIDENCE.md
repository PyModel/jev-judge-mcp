# Release evidence

Per release: the certified operating points and any regression, taken from the latest recorded
eval run. A user deciding whether to upgrade reads quality deltas here, not only the commit list
in [`CHANGELOG.md`](CHANGELOG.md).

This page is a curated digest: [`docs/evals/README.md`](evals/README.md) and the recorded reports
under it are the source of truth, and where this page and a recorded file disagree, the recorded
file wins. No number here is machine-checked against them; the machine-checked pages are the
caps and defaults in [`docs/reference/limits.md`](reference/limits.md).

Entries are filled from recorded results only — [`docs/evals/README.md`](evals/README.md) and the
reports under `evals/reports/` — dated, with the pinned model. Nothing on this page is estimated,
remembered, or projected. "Not measured" is a valid entry and often the right one. No paid run is
required to fill an entry: when no new eval was recorded since the last one, the entry says that.

## What a certified operating point is

A threshold (an `auto_accept` or similar) with the most `auto` rows whose one-sided 95%
Clopper-Pearson upper error bound stays within the per-tool target, certified on the held-out
`locked_test` split (`evals/README.md` § Calibration). Until such a split is recorded, no
operating point is certified, and the frozen defaults in `src/jev_judge_mcp/policy/thresholds.py`
are parity defaults inherited from the reference implementation — not points fitted on Jev traffic.

## Current entry

### v0.4.1 — released 2026-09-26

- **Latest recorded eval run:** 2026-09-23, pinned `jev-1.13.0` (TypeSafe), recorded at revision
  `a9dc147` per the study records ([`docs/evals/README.md`](evals/README.md)). See the provenance
  note below.
- **Certified operating points:** none. No calibration block has certified a threshold on held-out
  data; the recorded `locked_test` corpus is below the size the bounds need
  (`evals/README.md` § Open gaps).
- **Recorded L3 judgment quality** (synthetic datasets, small n, no significance test):

| tool | n | primary metric | value | also |
|---|---|---|---|---|
| jev_classify | 6 | selective_accuracy_auto | 1.000 | auto_coverage 1.000, macro_f1 1.000, micro_f1 1.000 |
| jev_verify | 5 | contradiction_recall | 1.000 | macro_f1 1.000, brier 0.00004, ece 0.002, selective_accuracy 1.000, auto_coverage 1.000, invalid 0 |

  No accuracy claim beyond those eleven questions; the other tools have no recorded live eval
  ([`docs/tools.md`](tools.md) states this per tool).

- **Recorded L4 agent outcomes** (2026-09-23, same run): both agents (Claude Code
  `claude-sonnet-5`, Pi `ds4/glm-5.3-flash`) solved the same pairs with and without Jev and were
  slower with Jev on every pair both arms solved. Descriptive only; n small, no significance test.
- **Regressions vs the previous recorded run:** none comparable — the repo holds no earlier
  recorded L3 run to diff against.
- **Provenance:** the recorded run predates the tags `v0.2.0` through `v0.4.1`. Releases since
  changed tool response fields, evidence composition, and CLI/hook surfaces without a re-recorded
  eval. Read the numbers above as the last measured, not as certified for this tag; if your use
  depends on the difference, re-run the recorded paths (`evals/README.md` § What runs where) before
  upgrading.

## History

Entries begin at v0.4.1, when this page was added. Earlier releases predate the page; their
evidence is the 2026-09-23 run above, recorded before `v0.2.0`.

## Maintaining this page

The release process step that fills the next entry is documented in
[`CONTRIBUTING.md`](CONTRIBUTING.md) § Releases: before merging a release pull request, add the new
release's entry from the latest recorded eval run, or state that no new run was recorded since the
last entry.
