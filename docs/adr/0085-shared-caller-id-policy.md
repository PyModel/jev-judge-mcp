---
status: accepted
---
# Use one caller ID policy

Find, verify, and gate used to suffix colliding caller IDs. Their `renamed_ids` map was keyed by
the original ID, so two or more items sharing that ID could not each be matched back to a result.
Other tools refused different kinds of collisions. An omitted ID could also take a key supplied
by a later item.

## Decision (2026-11-19)

- Find and rerank candidates, classify items and classes, decide candidates, extract fields, and
  verify and gate evidence all use `ids.py`. Gate range uses the gate policy. Ask keeps its
  existing opaque question keys. Decide candidates and extract fields still require IDs.
- Sanitize supplied IDs with the reference rule. Refuse two supplied IDs that sanitize to the
  same key, including the empty key. Refuse a supplied ID that matches a reserved key. Both
  refusals return `invalid_arguments` before any provider request and name the originals and key.
- Allocate missing IDs after validating all supplied IDs. Use the positional key when free.
  Otherwise, scan indices starting at the list length, in input order, skipping all supplied and
  reserved keys. A single supplied ID that sanitizes to empty uses the same allocation rule.
- Decide reserves the enabled escape hatches. Verify reserves `none` when it asks a source
  question. Gate reserves `none` for source selection, `diff` for patch citations, `tests` for a
  supplied log, and the canonical keys derived from file paths. Gate gives repeated or aliased
  file paths distinct implicit IDs before reserving them.
- Results use canonical IDs. Remove `renamed_ids` and stop suffixing caller collisions. Preserve
  the opaque provider request keys already used by classify, rerank, decide, and extract.

## Consequences

Callers that need exact matches can choose unique canonical IDs in each list. Align the generated
IDs with supplied IDs using the documented positional and overflow rules. This decision supersedes
ADR-0031 and the caller rename amendments in ADR-0062. The parity registry records the changed
results and refusals, and the reference fixtures remain frozen.
