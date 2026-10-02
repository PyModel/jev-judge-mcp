---
status: accepted
---
# Shared owners for the review half, the file-list pass, and the hook runtime

The 2026-10-01 architecture review found three behaviours written twice or three times, each
copy drifting on its own: the per-file loop of a `[{path, patch}]` diff in `tools/review.py` and
`tools/gate.py` (two of the day's fixes — the file-count bound and the truncation flag — had to
land in both, and one of the two bugs existed only because the copies had diverged); the review
half's questions, thresholds, documents, and projection, defined in `review.py` and imported
across; and the three short-lived hooks' provider wiring (settings, redacting logs, resolution,
one bounded call, cancellation, close), where the stored key missing from the log filter had to be
fixed three times.

## Decision

- `tools/review_half.py` owns what a review *is*: `review_questions`, `ReviewSettings` and
  `review_settings`, `ReviewDocs` and `review_docs`, `ReviewHalf` and `project_review`,
  `ANTI_INJECTION`, `RUBRICS`. `review.py` re-exports them; `gate.py` and `files.py` import them.
- `tools/files.py` owns the file-list pass: `file_patches`, `file_list_refusal` (the joined-patch
  and file-count budgets, one sentence each), `review_file_list` (one review request per fitting
  file through its own `CapLedger`, returning `FileListReview`), `FileListReview.review_half` (the
  half both tools publish: `score_file`, `reviewed_files`, `file_actions`), `file_actions`, and
  `combined`. The tools keep what differs: the refusal's shape (jev_review raises `input_too_large`,
  jev_gate returns the isError payload), the purpose text, the zero-request shape, and jev_gate's
  verification ask after the files.
- `hook_runtime.py` owns the hooks' wiring: `prepare(provider, resolve=…)` (settings, the
  redacting log handler over `keyfile.redaction_values`, the model, the provider) and
  `judge(provider, state, questions, model)` (one call bounded by `PROVIDER_TIMEOUT_SECONDS`,
  shielded close, cancellation). Both return a typed `HookFailure` (`config`, `timeout`,
  `provider`, `cancelled`) the hook renders in its own words; the module writes nothing. A hook
  passes its own `resolve_provider` name so a test that patches the hook module still reaches it.
- `domain.answers.RUBRIC_SCORE_MAX` owns the 0..2 rubric scale; `validation/score.py`,
  `policy/review.py`, and `responses.py` read it.
- The actions the tools computed move to `policy/` (ADR-0002 amendment of the same date):
  `verify_claim_action`, `gate_claim_action`, `file_list_action`, `extract_call_action`.

## Invariants and their tests

- A behaviour that both file-list paths share has one implementation; `tests/unit/test_file_diff.py`
  exercises it through both tools and the parity replay keeps the wire byte-equal.
- Every hook's fail-open text and ask reason is the hook's; `tests/unit/test_hook*.py` pin them
  unchanged over the shared runtime.
- `make policy-coverage` keeps 100% branches over the moved decisions; `tests/unit/test_policy.py`
  carries one test per branch.
