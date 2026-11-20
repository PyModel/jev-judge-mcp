# ADR-0082 Actionable completion gate escalations

Accepted 2026-11-18.

## Context

Across 358 distinct archived gate responses, 333 escalated. The retained scores explained 131
escalations solely by low rubric confidence. Neither the rubric nor the source question asked
the provider to identify an offending line. An excerpt taken from the first line of a log
could be a header even when the claim verdict said contradicted.

## Decision

Keep the existing admission limits, rubric, accepted thresholds, tool names and request count.
Ask additional typed questions within the same provider request. For the review, select a
demonstrated failure category and a changed line. For a claim, select a line establishing the
opposite of that exact claim. The server resolves IDs to excerpts of submitted lines. Each
selection must validate and meet `auto_accept` to establish an escalation. A performance or
correctness regression with limited evidence can still hold the change for review.

Offer up to 32 line choices per question, sampling throughout long sources and interleaving
sources. Assign consecutive labels from `L1` to `L32`. Source text appears only in evidence.
Exclude diff headers, blank excerpts and caller notes. Removed lines may identify a
regression from deleting code. Only added lines of patches are eligible to refute a claim.
The full documents remain available to the provider. Reduce optional citation choices when
the envelope is near its byte limit. Publish how many lines were eligible and offered.
Optional peer context that exceeds the byte window is repacked in complete records. Identify
omissions on the affected file. Original documents over a required limit still receive the
existing typed refusal before provider dispatch.

Preserve the raw scores and probability distributions. Move an uncited concern to review.
For an unconfirmed contradiction, publish the effective verdict `unsupported` alongside
`reported_verdict`. For an invalid required answer, explicitly refuse and identify the provider
field. Keep file decisions in input order and take the worst file or claim decision, including
the existing hold for withheld files. Count detailed reasons across the change and select one
next check, prioritizing evidence and refusals, then missing context. A rubric hold points the
reviewer to an example changed line when available. Only a provider-selected line can establish
a defect. All other diagnostics remain available under `blockers`.

## Consequences

The response stays compatible with readers of the existing fields and three actions. Consumers
that used escalate to mean any uncertainty should treat both review and escalate as held.
Review-only and verification-only tools retain their own policies. A typed category and line
are model judgments. The bounded candidate set can miss a real defect, and a citation can be
wrong. Fixture replay tests cover the deterministic contract. Paired corpus mutations measure
detection separately from the change in disposition of archived scores.
