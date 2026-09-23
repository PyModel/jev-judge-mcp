---
status: accepted
---
# Invalid answers fail closed through one policy token

`verify_action` said that unknown confidence "belongs to the tool". That sentence named the wrong check. A valid relation with no confidence is still mapped to `review` at the `jev_verify` call site (`index.ts:216`) and still never enters `verify_action`, which takes a float. A missing or malformed answer is a separate rule. Each tool had copied it: review and gate wrote `escalate`, verify, classify, and compare wrote `review`, and extract, decide, find, and rerank wrote `invalid_response` with no Action key. Screen already had `screen_fail_closed`.

`fail_closed` returns only that token: `escalate`, `review`, or `status`. The tool lays out its own keys. `status` is not an Action and is not written into the payload. A helper that built each row would be as wide as the tools, and an `escalate` token on extract would change recorded output.

## Consequences

- Review and gate take `escalate`. Verify, classify, and compare take `review`. Extract, decide, find, and rerank take `status`, and their invalid rows have no `action` key.
- `jev_screen` stays on `screen_fail_closed`. It is not a `fail_closed` tool.
- Wire payloads are unchanged. This is not a divergence. Classify's generated-id check (`_opaque`, ADR-0031) is unchanged.
- The absent-confidence review on a valid verify relation stays at the call site.
