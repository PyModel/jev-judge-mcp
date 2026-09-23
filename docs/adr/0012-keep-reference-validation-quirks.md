---
status: accepted
---
# Keep the reference's per-tool validation quirks; Q5 is withdrawn

Q2, Q3, and Q4 are real differences between tools in commit `69ffb4b`. Python mirrors them. Q5 described a bug `jev_compare` does not have. Unifying the real ones would change which answers pass.

**Q2, keep (D1).** `validateChoiceAnswer` uses `PROBABILITY_SUM_TOLERANCE = 0.01 + 1e-12` (`lib.ts:11`). `jev_extract` inlines `<= 0.01` (`index.ts:1060`) and does not require `probabilities` to be a record. `Math.abs(0.99 - 1)` is `0.010000000000000009`, so a 0.99 sum fails extract and passes the shared check. The same gap shows up for `0.3 + 0.3 + 0.39`. Both constants stay. Moving either one changes which sums validate.

**Q3, keep (D2).** `jev_decide` stores a malformed requirement check as `answer: "invalid_response"` and leaves it in `checks` (`index.ts:639-643`). Contradiction warnings skip those rows (`index.ts:645-649`). The tool has no action field. A new status or warning would change the payload. The invalid check is already visible.

**Q4, keep (D3).** `jev_verify` rejects a relation whose `confidence` is present and malformed (`index.ts:191-192`): the row is `invalid_response` / `review`. Other tools coerce that confidence to null. The stricter rule stays.

**Q5, withdrawn.** `jev_compare` validates with `validateChoiceAnswer` (`index.ts:846-863`), which sets out-of-range confidence to null (`index.ts:1192-1196`). `answer.confidence ?? null` at `index.ts:860` adds nothing. No out-of-range confidence reaches compare's policy.

**Field order, keep (D5).** `jev_extract` awaits each field's regex before starting the next (`index.ts:990-993`). Results are assembled in caller order. There is no cross-field state. Duration is not in the output, including the 32 s worst case of 32 patterns each using the full 1,000 ms. Running fields concurrently later needs no divergence tag. The cross-call pool is ADR-0004.

## Consequences

- Extract tests must include a 0.99 sum and expect `invalid_response` there, and expect the shared validator to accept that same sum.
- Compare fixtures use an out-of-range confidence and expect the choice to stand with `confidence: null`, the same as `validateChoiceAnswer`.
- A quirk entry in the manifest cites `file:line` at `69ffb4b`.
