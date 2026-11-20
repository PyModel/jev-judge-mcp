# ADR-0083 Fail-closed tool results and provenance

Accepted 2026-11-19. Amends ADR-0062, ADR-0064, ADR-0066, ADR-0067, ADR-0077, ADR-0081 and ADR-0082.

## Decision

A requested source or defect category must validate before its row can earn auto. An invalid
auxiliary answer caps the row at review. A required judgment that fails validation still refuses
the decision. When verification has exactly one evidence item, policy treats it as the source
without publishing a source selection. A caller note alone cannot earn auto. The gate reports
`caller_note_only` only when that rule withheld auto.

Verification keeps its confidence action and reports `stands` only for verified claims at auto.
Agents act on the verdict. A contradicted claim at auto is confidently false.

Review and gate share changed-line diagnostics and demote uncertainty to review. A citation
uses the evidence ID sent to the provider. Diff headers are excluded outside hunks. Header-like
changes inside hunks remain citable. Patch shape requires a hunk or a file header pair.
`evidence_line` holds a source, line and excerpt. `invalid_answer` names the invalid provider
question or required rubric. A cited refusal alone sets `safety_refusal`. The gate emits the frozen
summary codes in `reason_codes`, detailed codes in the rollup and blockers, and one prioritized
next check.

Caller-supplied test text is self-reported. The CLI and range reader can supply server-read logs
through their internal calls. The gate and review schemas drop `tests_format` and `tests_sha256`.

The runtime owns the file provider fan-out cap for review and judgment batches. A judgment batch
with no calls reports its requested model. A batch whose calls all fail names the attempted
provider. Choice question keys obey the shared short-identifier limit.

The CLI consumes the tool's action and unresolved status. Unexpected handler exceptions have the
redacted `internal` code, which a required completion hook treats as failure to reach the provider.

## Amendment (2026-11-20)

When the model explicitly selects `none` as the source for a verified claim, gate and verify
hold the claim for review. Policy version 3 adds `claim_verified_without_source` to explain
the hold. A single implicit evidence item in verify still follows the single-item rule above.

## Consequences

The divergence registry records the result and schema changes. Parity projects the approved
changes over the immutable recordings. Public tool tests exercise the new cases at the call
boundary.
