---
status: accepted
---
# Choice confidence is absent, malformed, or a number

`confidence_of` turns both a missing confidence and a present malformed one into `None`. `jev_verify` then re-read the raw relation to apply Q4 (ADR-0012): a present malformed confidence invalidates the relation, and an absent one is a review. That re-read is the leak. The wire number cannot carry the distinction, because other tools write `null` for both and the recorded payloads stay that way.

`ChoiceAnswer.confidence` remains that wire number. `confidence_kind` is `absent`, `malformed`, or `number`. `validate_choice` sets it from the raw answer. `jev_verify` compares the kind and does not read the relation again. A constructor that is given only the wire number treats a float as `number` and `None` as `absent`; it cannot recover `malformed`.

## Consequences

- Q4 is unchanged. Malformed confidence still invalidates. Absent confidence, including JSON null, still reviews. Tool JSON is unchanged. This is not a divergence.
- Classify, compare, decide, extract, and gate still write `answer.confidence` and still ignore the kind. A malformed confidence there stays a valid answer with `null`.
- `validate_extract_choice` still passes `confidence_of` into the constructor, so an extract answer cannot tell malformed from absent. Extract does not apply Q4.
