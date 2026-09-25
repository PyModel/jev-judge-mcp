---
status: accepted
---
# Gate claim checks may use diff and tests

Claim questions told the model to ignore `diff` and `tests`. A claim whose only support was the patch came back unsupported until the caller pasted the patch into `evidence`.

## Decision

- Before the claim questions, the gate adds implicit evidence items `diff` and, when tests is non-empty, `tests`.
- The sentence that forbids using them is removed. The anti-injection sentence stays.
- A truncated diff never makes a claim `auto`. `require_complete_context` still demotes `auto` when context was cut. An unreviewed file from a multi-file diff is the same rule.
- This is a question-text divergence. Recorded gate requests are checked against the new question, not the frozen sentence.

## Consequences

- Callers no longer have to paste the patch into `evidence` for the claim half to see it.
- A hostile or truncated diff is still evidence the model can misread. Policy, not the question, keeps `auto` off when the diff was cut.
