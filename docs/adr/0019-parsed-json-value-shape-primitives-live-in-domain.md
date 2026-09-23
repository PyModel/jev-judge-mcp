---
status: accepted
---
# Parsed-JSON value-shape primitives live in `domain/json.py`

Providers were importing `is_answer_record` and `as_number` from `validation/numbers.py` — a sideways dependency: the Envelope layer reaching into Answer-validation code for what is really a question about a parsed JSON value's shape (`index.ts:1170-1172`, JS `typeof`). With P5's tools importing the same primitives, the sideways arrow would only thicken against ADR-0002's one-way layers.

The primitives moved to `domain/json.py` (`is_answer_record` renamed `is_json_object`, which is what it means at an Envelope call site); `validation/` keeps the semantic validators (`unit_interval`, `confidence_of`, the `validate_*` family) and imports from below.

The module's charter is narrow on purpose: **parsed-JSON value-shape semantics only** — JS `typeof` equivalence and JSON object shape. Anything about answers, confidence, providers, questions, or normalization belongs above it. `domain/json.py` must not become a home for "things that were inconvenient somewhere else" (ADR-0019's reason to exist is the same as its reason to stay small).

**Amendment — reading JSON text.** `decode_json` also lives here: JSON text read as `JSON.parse` reads it, which in Python means refusing the `NaN`/`Infinity` constants `json.loads` accepts. The stdio wire and provider response bodies both read JSON this way, so one strict decoder owns the rule; what each does with a rejected text (drop the frame, treat the body as `null`) and any byte-level decoding (UTF-8 replacement, a body's leading BOM) stay with the caller.

## Consequences

- `providers/` and `validation/` both import from `domain/`; nothing in `domain/` imports from either.
- The old name is gone; call sites say what they check (`is_json_object`), not where they were first used.
