---
status: accepted
---
# ADR-0081. Explain held gates and bound the context shared between file reviews

## Context

The frozen review returns the worst action across safety, rubric confidence and the composite.
A file list can publish a different file's score vector from the file that caused that action.
The rubric on an implementation file could not see a sibling regression test or caller evidence.

## Decision

Extend ADR-0062's response fields and amend the per-file state in ADR-0066. A non-auto gate
publishes ordered `blockers`. Each identifies its field, value, comparison and threshold where
applicable, a focused next check, and its file or claim where applicable. Rubric confidence
includes the observed score and its interpreted level. A composite miss includes all levels.
Invalid responses name the missing usable field. Truncations report UTF-16 lengths and limits.
The gate adds specific codes and next checks alongside existing summaries. A review also
publishes its own blockers. For a file list, `score_file` identifies the first file with the
worst review action. Each file blocker gives its path and index, including in its next check.
The gate asks the rubric about the target file's contribution and its cross-file contracts.
ADR-0002 defines the numeric and claim policies and the aggregation rules.

Each fitting file receives its own complete patch, request and dedicated tests. Supplemental
context includes its path, complete caller evidence records in order, and complete fitting
sibling patches in input order. Use the existing budgets: per review the allowance is the
smaller of one document and the aggregate divided by the number of fitting files, rounded down.
Count the compact JSON in UTF-16 including delimiters. Skip records too large for the remaining
allowance and continue packing shorter records. When a supplemental record is omitted, the
review publishes an informational `context_omissions` list in reviewed-file order. Each row gives
the target `path` and `reviewed_file_index`, the full `required_units` and `limit_units` in UTF-16,
and the omitted `path`, `evidence[i]` or `reviewed_files[i]` entries. A gate returns this list in
its `review` object. Supplemental omissions leave actions, summary codes, next checks and coverage
flags unchanged for the same judgments. The gate refuses oversized primary documents before
any provider call. This supersedes ADR-0066's partial result for oversized gate patches;
`jev_review` retains its partial review. Before fan-out the gate checks every review and the
canonical verification. Compact UTF-8 must fit 30,000 bytes for state plus the longest
question and 60,000 bytes for the whole request. These conservative limits leave room for
provider framing under its 32k and 64k token windows. Budget errors name the bound and describe
how to split by file, hunk or claim. A git range that withholds a file names its path and reason,
marks coverage partial, and can return at best review even when every examined file passes.
Completion claims and source questions still run once against the canonical evidence.

## Consequences

At most one existing aggregate allowance of supplemental context is replicated across file
reviews. Output can grow with the number of held files and claims. A low confidence can coexist
with a favorable level. A next check identifies evidence to inspect. Only an independent check
can establish the specific defect. The server's git boundary and provider token limit still apply.
The local CLI can read a different working tree from its own process. Its bounded git reader
returns `input_too_large` on overflow, matching the range tool and the CLI's other budget errors.
