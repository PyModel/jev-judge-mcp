---
status: accepted
---
# Evidence may name its kind, role, and whether tests were hashed

Every evidence item used to be raw text. A caller note verified a claim. A before/after blob had no role. A test log was a string with no hash.

## Decision

- Optional `kind`: `raw` (default), `diff`, `tool_output`, `caller_note`. A claim supported only by a `caller_note` is not `auto` and gets reason `caller_note_only`. The note stays in the state. Policy discounts it. The model still judges.
- Optional `role`: `before`, `after`, `current` (default). The question text says a claim is about `current` unless it says otherwise, and to use `after` or `current` items for that claim.
- Optional `tests_format` (`text`, `junit`, `tap`) and `tests_sha256`. Unhashed text tests are marked `tests_weight: self_reported` on the review payload. The hash is only set by a reader that read the file. The MCP tool does not hash a string the caller typed.
- `GATE_REASON_CODES` gains `caller_note_only`. `policy_version` bumps with that tuple.

## Consequences

- Old calls omit the fields and stay `raw` / `current`. Their actions do not change unless a caller sets `kind` to `caller_note`.
- A fabricated test line is marked self-reported. It is not parsed as JUnit.
