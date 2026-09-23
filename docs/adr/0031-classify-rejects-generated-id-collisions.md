---
status: accepted
---
# Classify rejects a generated id that collides with a supplied id

`jev_classify` fills an omitted id with `{kind}{index}` (`item0`, `class0`, and so on). The reference does not check that string against ids the caller supplied (`index.ts:398-526`). The probabilities object is keyed by the external id, so the later row overwrites the earlier one. Items `[{text}, {id: item0}]` come back as two results with `id` `item0`, both `auto`. The same collapse hits classes, in `probabilities` and `by_class`. The recording is `duplicate-id/classify-omitted-ids-get-positional-fallbacks`. Supplied duplicates were already `Duplicate {kind} id` before any provider request (`index.ts:441`, `index.ts:450`). `jev_rerank` already keeps a generated id off every supplied id (`index.ts:711-722`).

This is a Reference Quirk (Q11). Keeping it returns the wrong classification at `auto`. Python diverges. After each id is chosen, supplied or generated, a repeat raises the existing `Duplicate {kind} id` `ToolError` and no provider request is made. Items and classes both. Two omitted ids still become `item0` and `item1`. Those do not collide.

## Consequences

- The fixture stays a recording of the reference and is tagged `ADR-0031`. The parity expectation is the tool error and zero provider requests. Items are checked first, so this recording fails on `item0`.
- `ids.duplicate_caller_ids_rejected_in` still describes supplied ids. Q11 is the generated-id case.
- Moving every tool's id allocation into `ids.py` is a separate change.
