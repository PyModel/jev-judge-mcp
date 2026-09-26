---
status: accepted
---
# A file list is reviewed per file, not silently cut

A string `diff` over the document cap is truncated and cannot be `auto`. That inherited rule stays for a string. A caller who has the bytes per file should not have to hope the join fits.

## Decision

- `diff` accepts the string it accepts today, or `[{path, patch}]`.
- The array is split by file. Each file is reviewed under the per-document cap. The call's action is the worst file action.
- A file or hunk that does not fit is listed in `unreviewed_files`. The payload sets `partial` true. The call never returns `auto` while any file is unreviewed.
- The joined array is refused past the 200,000-unit evidence budget, the same budget as gate evidence. That refusal is an error, not a silent cut.
- A string that is a unified diff is what `gate` the CLI passes as this array. A string argument to the MCP tool still follows the inherited truncation rule.

## Consequences

- Two files under the cap can be `auto` when each review is `auto`, even if joining them would have been truncated.
- One bad file escalates the whole call.


## Amendment (2026-09-26): the gate's file list verifies claims once

`jev_gate`'s file-list path first re-ran the whole string-diff gate per file: every per-file
request carried the full rubric plus every claim and source question and the entire evidence, so
N files billed N full requests, N−1 claim verdicts were discarded, and the payload kept only the
last file's rows — which could contradict the call's worst-action headline.

The split path now asks two kinds of request and frames one reply over all of them:

- One review request per fitting file: state `purpose, request, diff, tests` (no claims, no
  evidence) and the plain `jev_review` rubric questions. The review half of the payload is the
  first reviewed file's half with the worst file action, plus `score_file` and `reviewed_files`,
  exactly as `jev_review`'s file list reports them.
- One verification request after the files: state `purpose, request, claims, evidence` and only
  the `claim_i` and `source_i` questions, asked once. The fitting files join the evidence as one
  implicit `diff` item per file (`id "diff:<path>"`, sanitized like any id), so a claim can rest
  on a file's patch the way a claim rests on the string diff. The `verification` block is this
  one request's rows — canonical by construction — and `renamed_ids` covers this request's
  evidence pass.

The call's action is the worst of the review files' worst action and the verification action,
and `usage` is the sum over every provider call with a request id kept, through the same
`combined` helper `jev_review` uses. The evidence budgets are refused once in `handle()`, before
the split, so a file list over budget is an `isError` exactly like a string diff. A string `diff`
keeps its one-request shape unchanged.

Three shapes on the same path, settled with the amendment (2026-09-26): an unreviewed file is
incomplete context even when nothing was cut, so the clamp that keeps an otherwise-auto call at
`review` carries `incomplete_context` and its next check, while the payload's `truncated` field
stays false (it reports cuts, not unreviewed files). A file list whose every file is oversized
returns the framed zero-request shape `jev_review` uses for the same case (`model`,
`provider: "none"`, `usage: null`) instead of a bare dict. The joined-patches refusal names the
diff (`diff exceeds the 200,000-character aggregate budget`, worded like `jev_review`'s
overflow), not the evidence, and keeps the `input_too_large` code.
