# Tool cards — intended use, measured evidence, weak spots

One card per published tool: what it is for, what this repo's own recorded evidence says about it,
and when not to use it. Claims here come only from recorded results
([`docs/evals/README.md`](evals/README.md), [`evals/README.md`](../evals/README.md)) and from
behavior the code and ADRs freeze. Where nothing is recorded, the card says so: "no recorded live
eval" is a statement about evidence, not a verdict that the tool works. Routing (which tool fits
which step) is in [`docs/skills/jev-mcp/SKILL.md`](skills/jev-mcp/SKILL.md); caps and defaults in
[`docs/reference/limits.md`](reference/limits.md).

The one recorded live judgment-quality run to date (2026-09-23, pinned `jev-1.13.0`, synthetic
datasets, 3 cases per tool) covers `jev_classify` and `jev_verify` only. Every other tool below is
unmeasured live; its "weak spots" are frozen behaviors and documented gaps, not measured failure
rates. Certified operating points: none yet ([`docs/EVIDENCE.md`](EVIDENCE.md)).

---

## jev_verify — claims against evidence

**Use when** you hold claims (a summary, a PR description, an agent's "done" list) and the evidence
they cite, and want verified / contradicted / unsupported per claim.

**Measured.** L3 live run, 2026-09-23: n=5 claims, contradiction_recall 1.000, macro_f1 1.000,
brier 0.00004, ece 0.002, selective_accuracy 1.000, auto_coverage 1.000, invalid 0. Five synthetic
questions; no accuracy claim beyond them.

**Weak spots.** Claims, evidence, and their lengths are deliberately uncapped, so cost and latency
scale with what you send (README § Operator notes). Policy has two tiers only — `auto` and
`review`; there is no escalate (manifest `policy.verify_action`). An unsupported or contradicted
claim can carry a `missing_evidence` code (`single_item_no_source`, `needs_diff`, `needs_tests`,
`needs_before_after`) that names what evidence would settle it.

**Not for** judging a claim you cannot supply evidence for, or producing analysis text.

## jev_screen — read a fetched page before reading it

**Use when** an agent is about to ingest external text and needs injection risk and relevance first.

**Measured.** No recorded live eval. The scorer exists (`injection_recall_at_false_block_rate` at a
fixed false-block rate; `evals/README.md`), but the fixed false-block rate has no specified value,
so no run was recorded.

**Weak spots.** The `skip` thresholds for substance and relevance are hardcoded at 0.3 in the
reference and are not call arguments (ROADMAP P7 notes this as the current hardcoding case);
`block_at` and `review_at` are call arguments. A malformed answer fail-closes to `review`, not
`block`. This is a triage of one text you already hold, not a malware or phishing detector.

**Not for** deciding whether to trust content after reading it, or screening binary formats.

## jev_classify — many items, one catalog

**Use when** many items each belong to one class from a catalog you can write down (keep/drop,
routing, clutter/article), and you want one call.

**Measured.** L3 live run, 2026-09-23: n=6 items, selective_accuracy_auto 1.000, auto_coverage
1.000, macro_f1 1.000, micro_f1 1.000. Six synthetic items; no accuracy claim beyond them.

**Weak spots.** An item stands `auto` only when the top probability clears `auto_accept` (0.85) and
the margin over the runner-up clears `minimum_margin` (0.5) — two conditions, so a split between
two lookalike classes lands `review` even at high top probability. Conflicting caller ids are
rejected before any provider request (ADR-0031). Item text cut at the cap is reported to telemetry
only and does not change the action (CONTEXT.md "Truncated Context" draws this line).

**Not for** labels you cannot enumerate, or when one item needs its own question — that is a batch
of noul or choice questions, not a catalog.

## jev_decide — one bounded choice

**Use when** one decision has between 2 and 6 options you can name, and declining is a real answer.

**Measured.** No recorded live eval. The scorer's primary metric is `overdecision_rate` (picking a
candidate when it should have escaped), with escape-hatch accuracy and requirement-check F1.

**Weak spots.** The escape hatches (`ask_user`, `investigate`, `none`) are always offered; a
candidate id that collides with one is refused. Requirements are capped at 3, each up to 500 UTF-16
units — a decision with a longer rulebook does not fit and belongs in code (ADR-0002), not in the
question.

**Not for** open-ended planning, ranking, or anything with more than six options.

## jev_find — which candidate answers

**Use when** you have a set of candidate documents/notes and need the best one for a question, plus
whether any answers at all.

**Measured.** No recorded live eval. Scorer: recall@1, exists AUROC, MRR, NDCG@10, verdict
accuracy.

**Weak spots.** The exists verdict thresholds (answered at ≥ 0.7, absent below 0.35, else
`partial`) are fixed defaults, not call arguments. Candidate text is truncated at the cap
(telemetry only). `top_k` defaults to 5.

**Not for** discovering candidates — it only ranks what you pass, unlike a search engine.

## jev_rerank — order candidates you already have

**Use when** search hits or grep results need a relevance order and every candidate should get a
score.

**Measured.** No recorded live eval. Scorer: NDCG@10 and Kendall τ against BM25 and original
order; an embeddings baseline is an open gap, not implemented (`evals/README.md`).

**Weak spots.** A flat band of low `relevance` values means the candidates were not
distinguishable; treat the order as weak (README § Operator notes). `ranked[].relevance` is the
probability at a fixed precision; there is no spread field. Aggregate candidate text over 100,000
UTF-16 units refuses with an `input_too_large` error.

**Not for** deciding whether anything is relevant at all — that is `jev_find`'s exists question.

## jev_compare — two passages, one fact relation

**Use when** two passages should state the same fact and you need same / contradiction / different
facts, optionally per aspect (up to 10).

**Measured.** No recorded live eval. Scorer: contradiction recall on numeric/negation
perturbations, aspect-level F1.

**Weak spots.** Passages are schema-rejected above 20,000 UTF-16 units each — long documents must
be cut by the caller, and the cut decides what gets compared. When aspect verdicts contradict the
overall verdict, the result carries a warning rather than silently agreeing (ADR-0052).

**Not for** whole-document diffing or stylistic comparison; it judges fact relation, not text
difference.

## jev_extract — a value your regex proposes

**Use when** a value (version, date, price) sits in a document and your regex can propose the
candidates; Jev picks among them or returns null.

**Measured.** No recorded live eval. Scorer: exact match, hallucinated values (must be 0),
not-found precision/recall.

**Weak spots.** The invariants are hard (ADR-0004, ADR-0018): the value is one of the verbatim
regex matches or null; a timing-out pattern returns `invalid_pattern`; a capped or too-long
candidate universe is never `auto` and never a definite `not_found`. The regex dialect is a narrow
subset of `re`; a pattern outside it refuses with `invalid_pattern` rather than misbehaving.

**Not for** values a regex cannot propose, or fuzzy matching — the universe of candidates is
exactly what your pattern matched.

## jev_review — is this patch acceptable

**Use when** a diff should be graded against its request before the task is called done.

**Measured.** No recorded live eval. Scorer: P(defective | auto), safe_to_apply AUROC,
severe-defect recall.

**Weak spots.** Escalate here is uncertainty, not a finding: the lowest rubric confidence below
`review_at` (default 0.5) escalates, including a low-confidence ancillary score like `test_gap` on
a patch the other scores accept (README § Operator notes). The rubrics are fixed
(correctness, spec_match, test_gap, blast_radius); callers cannot add one. A string `diff` over
the cap is truncated and judgment over cut context never stands `auto`; a file-list `diff` is
reviewed per file instead — one oversized or unreviewable file lists in `unreviewed_files`, marks
the call `partial`, and keeps it off `auto` (ADR-0066).

**Not for** producing a rewritten patch, style review, or replacing the test run — it reads what
you pass (ADR-0063: diff and tests count as evidence when you do not pass them explicitly).

## jev_gate — did the work finish

**Use when** a patch plus completion claims plus test logs should produce one ship decision with
per-claim verdicts. The completion check; call it before claiming done on a diff.

**Measured.** No recorded live eval. The design target is a false-AUTO rate accepted on its
Clopper-Pearson upper bound, not a point estimate (ROADMAP headline metrics); certifying the 0.5%
target needs roughly 600 error-free held-out rows per run, and that corpus is not recorded yet
(`evals/README.md` § Open gaps).

**Weak spots.** Same escalate-is-uncertainty rule as jev_review, plus the claim rules: a
contradicted claim escalates, and cut context (diff, tests, docs, evidence over their caps) keeps
the gate off `auto`. A file-list `diff` is reviewed per file with the claims verified once; a file
over the cap lands in `unreviewed_files` with reason `incomplete_context` and the whole gate
turns on the worst file (ADR-0066). Evidence is capped at 16 items and 200,000 aggregate UTF-16
units; over either is an `input_too_large` error telling you to split the gate.

**Not for** a substitute for CI or tests — it judges the claims you pass against the evidence you
pass, and never runs anything itself.

## jev_score — your ordered rubric

**Use when** severity, quality, or risk should land on an ordered scale of 2 to 10 levels you
write, with the full per-level distribution.

**Measured.** No recorded live eval, and no bench corpus yet (extension tool, ADR-0048). Scorer:
nearest-level accuracy, level MAE, within-one rate.

**Weak spots.** Positions between levels are weakly calibrated: `score` is a probability-weighted
position, not a magnitude — threshold it against levels, do not interpolate (README tool table).
Levels and text bounds are owned by ADR-0048, not by a parity-manifest block. One call, one rubric:
several scales are several questions.

**Not for** continuous magnitudes, or when you need an explanation — the answer is a distribution,
never text.
