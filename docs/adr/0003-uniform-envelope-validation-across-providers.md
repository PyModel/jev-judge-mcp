---
status: accepted
---
# Validate the transport envelope identically for every provider

In the reference only the `compatible` provider checks the response envelope (object body, `answers` object, well-formed `usage`, string `model`); TypeSafe, OpenRouter and Cloudflare coerce a missing `answers` to `{}`, so each tool then reports per-item `invalid_response`. Python applies the `compatible` rules to every provider and raises a provider error instead. This is a Sanctioned Divergence (quirk Q1): both behaviors fail closed, but a broken envelope is a transport failure, not a set of per-question judgments, and reporting it as one hides outages behind plausible-looking results.

## Consequences

- Parity fixtures for "missing/non-object answers" from non-compatible providers expect a tool error in Python, and are tagged `divergence:ADR-0003`.
- Per-answer validity (shapes, bounds, argmax) is still each tool's job, unchanged.
