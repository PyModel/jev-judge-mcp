# Input caps, defaults, and error codes

Every cap and default this server enforces, the error code each refusal produces, where thresholds
live, and how to re-check all of it. Nothing on this page is tunable at runtime: the caps are
frozen (ADR-0014), transcribed in `src/jev_judge_mcp/limits.py` from the parity manifest
(`docs/reference/parity-manifest.json`), and the thresholds are frozen defaults in
`src/jev_judge_mcp/policy/thresholds.py`. The tables below are machine-checked:
`tests/contract/test_docs_alignment.py` fails if a value here drifts from the code, and
`tests/contract/test_limits.py` fails if the code drifts from the manifest. A re-freeze updates
all three in one change.

Text lengths are UTF-16 code units, JavaScript's `.length` (ADR-0005) — not Python code points.
Astral-plane characters (emoji, some CJK) count as two.

## The three behaviors at a bound

1. **Reject** — the argument schema refuses the call. The result is an `isError` result whose text
   starts `MCP error -32602: Input validation error: Invalid arguments for tool <tool>: …`; its
   code is `invalid_arguments`. Nothing is sent to the provider.
2. **Truncate** — the text is cut to the cap, marked ` […truncated]`, and the result carries
   `truncated` scopes. Not an error. A cut over *context* (a document the judgment is made over:
   review/gate inputs, gate claims and evidence, extract's candidate universe) keeps the action off
   `auto`; a cut over *item* text (find/rerank candidates, classify items and class descriptions)
   is telemetry only and does not change the action ([`docs/CONTEXT.md`](../CONTEXT.md) "Truncated
   Context").
3. **Budget error** — an aggregate over several arguments, a strict greater-than check: the cap
   itself is allowed, one unit over is not. The result is an `isError` result with code
   `input_too_large` and a frozen remedy sentence (`split the batch`, `split the gate or trim the
   evidence`).

## Error codes

Every `isError` result carries its code twice: in `structuredContent.code` and in a second text
block (ADR-0062). `src/jev_judge_mcp/responses.py` `error_code` is the mapping.

| Code | Produced by |
| --- | --- |
| `invalid_arguments` | a schema reject (every row marked reject below); an unknown tool; duplicate caller ids; a decide candidate id colliding with an escape hatch; a diff that is not the file-list shape; a broken `auto_accept`/`review_at` pair (the frozen text `Thresholds must satisfy 0 <= review_at <= auto_accept <= 1.`) |
| `input_too_large` | the aggregate budget errors marked error below |
| `auth` | no provider credentials; the hook's fail-open text |
| `timeout` | a provider timeout |
| `quota` | HTTP 429 / rate limit |
| `provider` | any other provider failure |

## Input caps, per tool

`jev_score`'s caps have no parity-manifest block: ADR-0048 owns them. Every other table is the
manifest's `caps` block. `no cap` means the reference deliberately leaves the field open — the
manifest records null and a bound must not be added.

### jev_verify (`limits.VERIFY`)

| Cap | Value | Over the bound |
| --- | --- | --- |
| `claims_min` | 1 | below → reject |
| `claims_max` | no cap | — |
| `claim_units` | no cap | — |
| `evidence_min` | 1 | below → reject |
| `evidence_max` | no cap | — |

Uncapped on purpose: cost and latency scale with what you send (README § Operator notes).

### jev_screen (`limits.SCREEN`)

| Cap | Value | Over the bound |
| --- | --- | --- |
| `text_min` | 1 | below → reject |
| `text_max` | no cap | — |
| `purpose_max` | no cap | — |

### jev_find (`limits.FIND`)

| Cap | Value | Over the bound |
| --- | --- | --- |
| `top_k_min` | 1 | below → reject |
| `top_k_max` | 50 | above → reject |
| `top_k_default` | 5 | the default when `top_k` is omitted |
| `query_min` | 1 | below → reject (a schema-only bound the manifest does not record; `tests/contract/test_limits.py` owns the tie) |

Candidate caps are the shared `CANDIDATES` table below.

### jev_classify (`limits.CLASSIFY`)

| Cap | Value | Over the bound |
| --- | --- | --- |
| `items_min` | 1 | below → reject |
| `items_max` | 64 | above → reject |
| `item_units` | 2000 | truncate (item scope) |
| `classes_min` | 2 | below → reject |
| `classes_max` | 250 | above → reject |
| `class_description_units` | 2000 | truncate (item scope) |
| `item_class_pairs` | 8000 | over → budget error (`input_too_large`), rendered `8,000` |

### jev_decide (`limits.DECIDE`)

Every bound is a schema reject; nothing truncates.

| Cap | Value | Over the bound |
| --- | --- | --- |
| `decision_min` | 1 | below → reject |
| `decision_max` | 1500 | above → reject |
| `evidence_min` | 1 | below → reject |
| `evidence_max` | 12000 | above → reject |
| `priorities_min` | 1 | below → reject |
| `priorities_max` | 2000 | above → reject |
| `candidates_min` | 2 | below → reject |
| `candidates_max` | 6 | above → reject |
| `candidate_id_max` | 64 | above → reject (ids must match `^[a-z][a-z0-9_-]*$`) |
| `candidate_description_min` | 1 | below → reject |
| `candidate_description_max` | 2000 | above → reject |
| `requirements_min` | 0 | below → reject |
| `requirements_max` | 3 | above → reject |
| `requirement_min` | 1 | below → reject |
| `requirement_max` | 500 | above → reject |

### jev_rerank (`limits.RERANK`)

| Cap | Value | Over the bound |
| --- | --- | --- |
| `query_min` | 1 | below → reject |
| `query_max` | 2000 | above → reject |
| `aggregate_candidate_units` | 100000 | over → budget error (`input_too_large`) |
| `top_k_min` | 1 | below → reject |
| `top_k_max` | 250 | above → reject; omitted `top_k` returns all candidates |

Candidate caps are the shared `CANDIDATES` table below.

### jev_compare (`limits.COMPARE`)

| Cap | Value | Over the bound |
| --- | --- | --- |
| `passage_min` | 1 | below → reject |
| `passage_max` | 20000 | above → reject |
| `aspects_min` | 0 | below → reject |
| `aspects_max` | 10 | above → reject |
| `aspect_min` | 1 | below → reject |
| `aspect_max` | 200 | above → reject |

### jev_extract (`limits.EXTRACT`)

| Cap | Value | Over the bound |
| --- | --- | --- |
| `document_min` | 1 | below → reject |
| `document_max` | 50000 | above → reject (then truncate at the same cap, a no-op guard) |
| `fields_min` | 1 | below → reject |
| `fields_max` | 32 | above → reject |
| `field_id_max` | 64 | above → reject (ids must match `^[a-z][a-z0-9_-]*$`) |
| `pattern_min` | 1 | below → reject |
| `pattern_max` | 500 | above → reject |
| `flags_max` | 8 | above → reject |
| `description_min` | 1 | below → reject |
| `description_max` | 2000 | above → reject |
| `candidates_per_field` | 20 | the matcher's per-field candidate limit |
| `candidate_units` | 2000 | over → the match is skipped and counted `tooLong`, never truncated |
| `aggregate_candidate_units` | 50000 | over → budget error (`input_too_large`) |
| `regex_timeout_ms` | 1000 | a pattern over it returns `invalid_pattern` (its own copy lives in `extract/candidates.py`) |

### jev_review (`limits.REVIEW`)

| Cap | Value | Over the bound |
| --- | --- | --- |
| `doc_units` | 50000 | a string `request`/`diff`/`tests` truncates (context scope). A file-list `diff` is reviewed per file under the same cap: a file over it is listed in `unreviewed_files`, the payload is `partial`, and the call never stands `auto`; the joined list over the shared aggregate budget (`GATE.aggregate_evidence_units`) refuses with `input_too_large` (ADR-0066) |

### jev_gate (`limits.GATE`)

| Cap | Value | Over the bound |
| --- | --- | --- |
| `claims_min` | 1 | below → reject |
| `claims_max` | 16 | above → reject |
| `claim_units` | 2000 | truncate (context scope) |
| `evidence_items` | 16 | over → budget error (`input_too_large`) |
| `aggregate_evidence_units` | 200000 | over → budget error (`input_too_large`), rendered `200,000` |
| `doc_units` | 50000 | a string `request`/`diff`/`tests`/evidence item truncates (context scope). A file-list `diff` is reviewed per file under the same cap: a file over it is `unreviewed_files` with reason `incomplete_context`, and the call never stands `auto`; the joined list over the aggregate budget refuses with `input_too_large` (ADR-0066) |

### jev_score (`limits.SCORE`, ADR-0048)

Every bound is a schema reject.

| Cap | Value | Over the bound |
| --- | --- | --- |
| `levels_min` | 2 | below → reject |
| `levels_max` | 10 | above → reject |
| `level_units_min` | 1 | below → reject |
| `level_units_max` | 200 | above → reject |
| `subject_min` | 1 | below → reject |
| `subject_max` | 1500 | above → reject |
| `context_max` | 12000 | above → reject |

### shared caps

| Cap | Value | Over the bound |
| --- | --- | --- |
| `min_items` | 1 | below → reject (jev_find, jev_rerank candidates) |
| `max_items` | 250 | above → reject (jev_find, jev_rerank candidates) |
| `text_units` | 2000 | truncate (item scope; jev_find, jev_rerank candidate text) |
| `SANITIZE_ID_UNITS` | 64 | the reference truncates a sanitized id at 64 UTF-16 units — a Python-side cap the manifest does not record (`ids.py` imports it) |

## Defaults and thresholds

Every threshold a tool compares against lives in `src/jev_judge_mcp/policy/thresholds.py`
(ADR-0002); the manifest's `defaults` block is the frozen record, and `policy_version` (currently
`2`) bumps when a default changes. Rows marked "not a parameter" are hardcoded in the reference
and cannot be set per call.

| Constant | Default | Used by |
| --- | --- | --- |
| `DEFAULT_AUTO_ACCEPT` | 0.8 | jev_verify, jev_review, jev_gate (`auto_accept`, a call argument) |
| `DEFAULT_REVIEW_AT_CAP` | 0.5 | an omitted `review_at` becomes min(0.5, auto_accept) on jev_review, jev_gate |
| `DEFAULT_COMPOSITE_FLOOR` | 0.7 | jev_review, jev_gate (`composite_floor`, a call argument) |
| `DEFAULT_CLASSIFY_AUTO_ACCEPT` | 0.85 | jev_classify, jev_compare, jev_extract (`auto_accept`, a call argument) |
| `DEFAULT_MINIMUM_MARGIN` | 0.5 | jev_classify, jev_compare, jev_extract (`minimum_margin`, a call argument) |
| `DEFAULT_SCREEN_BLOCK_AT` | 0.75 | jev_screen (`block_at`, a call argument) |
| `DEFAULT_SCREEN_REVIEW_AT` | 0.25 | jev_screen (`review_at`, a call argument) |
| `SCREEN_SUBSTANCE_SKIP_BELOW` | 0.3 | jev_screen skip rule — not a parameter |
| `SCREEN_RELEVANCE_SKIP_BELOW` | 0.3 | jev_screen skip rule — not a parameter |
| `EXISTS_FOUND_AT` | 0.7 | jev_find exists verdict (answered) — not a parameter |
| `EXISTS_ABSENT_BELOW` | 0.35 | jev_find exists verdict (absent) — not a parameter |

Other defaults, in prose: the model is `jev-latest` unless `JEV_MCP_MODEL` says otherwise (README
§ Configuration); `jev_find`'s `top_k` defaults to 5; `jev_rerank`'s omitted `top_k` returns every
candidate; `jev_decide`'s escape hatches (`ask_user`, `investigate`, `none`) are always offered.
Thresholding guidance — including raising your own bar before destructive steps — is in
[`docs/guidance.md`](../guidance.md); what is certified on recorded traffic is in
[`docs/EVIDENCE.md`](../EVIDENCE.md).

## How to re-check this page

```sh
uv run pytest tests/contract/test_docs_alignment.py   # this page against limits.py and policy/thresholds.py
uv run pytest tests/contract/test_limits.py           # limits.py against the parity manifest
make parity                                           # frozen error texts, byte-identical (needs Node 24)
make eval                                             # offline eval scorer and calibration tests
make eval-live                                        # recorded live judgment quality (paid, guarded; evals/README.md)
```

The recorded eval numbers themselves — what was measured, when, on which pinned model — live in
[`docs/evals/README.md`](../evals/README.md); re-render the agent-outcome report offline with
`cp -R docs/evals/agent-outcomes evals/reports/agent-outcomes && uv run python -m evals.ab.run
--report-only`.
