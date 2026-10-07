---
status: accepted
---
# An upstream context overflow is input_too_large

The caps in `limits.py` are not Jev's context window. jev-1.13 accepts 64k tokens per request and 32k for `state` plus the longest question. jev_gate sends the diff and the tests log twice, once in the review state and once as implicit evidence (ADR-0063). So a call inside every cap can still overflow. The API answers 400 with `{"detail": {"error_type": "max_tokens_exceeded"}}`. That was coded `provider`, which reads as an outage. The real cause is the caller's input size, and the caller fixes it by splitting the call.

## Decision

- `_status_error` reads `detail.error_type` from the error body's JSON structure at the raise site and records it on the exception as `error_type`. Only a bare lowercase identifier is kept, so a body that reflects caller text or a credential cannot carry it onto that field. `evaluate`'s redacting re-raise keeps it, as it keeps `status`.
- `code_of` maps status 400 with `error_type == "max_tokens_exceeded"` to `input_too_large`. It still never reads the error text. A body that only names the token as a string, or another status, stays `provider`.
- The error text is unchanged. Only the code changes (ADR-0062).
- The server still does not count tokens and adds no new cap. A character estimate cannot predict tokens, and a new bound would diverge from the reference caps. The provider stays the one authority on its own window.
- This is divergence `upstream-context-overflow-is-input-too-large`.

## Consequences

- A client that branches on `input_too_large` already splits or trims. It now does that for an overflow too, instead of treating it as an outage.
- On the file-list path (ADR-0066), the per-file reviews are paid for before the one verification ask overflows. The code tells the caller to split. It does not refund those reviews.
