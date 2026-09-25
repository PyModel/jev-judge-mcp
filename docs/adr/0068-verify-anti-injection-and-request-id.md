---
status: accepted
---
# Verify carries the anti-injection sentence; request id is not invented

Gate and review already tell the model that state is evidence, not instructions. Verify's relation question did not. The payload names `model` and did not name a provider request id.

## Decision

- Verify's relation question, and its source question, end with the same anti-injection sentence gate uses. That is a question-text change. It is measured by verify's own eval fixture, not by assuming the score is unchanged.
- `request_id` is returned only when the provider response carries one (`request_id` in the envelope, or a `request-id` / `x-request-id` / `x-typesafe-request-id` header — the last is the name live TypeSafe sends). The server does not invent an id.
- `JEV_MCP_MODEL` is the model pin. The tool argument `model` stays stripped. There is no deterministic mode.

## Consequences

- A client cannot cite a made-up id as reproducibility.
- Verify fixtures are a registered question-text divergence. The recorded answers are replayed; the sent instructions gain the sentence.
