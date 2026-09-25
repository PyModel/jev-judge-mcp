---
status: accepted
---
# Additive response fields stay beside the old keys

Gate and verify summaries counted verdicts and actions in overlapping buckets. Claim rows omitted whether the row stands, and gate rows omitted the id and source pointer verify already had. Review scores are 0..2 and the payload did not say so. Reason codes did not say what to do next. Errors were prose only.

## Decision

- Old keys stay, in their old order. There is no `uncertain` verdict.
- Gate and verify summaries gain `by_verdict` and `by_action`. Each sums to the claim count.
- Claim rows gain `stands` (true only for `auto`), `evidence_ids`, a capped excerpt, and `missing_evidence` (`needs_diff`, `needs_tests`, `needs_before_after`, or `single_item_no_source`) on an unsupported or contradicted claim.
- Gate rows gain `id` and `supporting_evidence`. Gate asks the source question verify already asks when more than one evidence item is sent.
- Review gains `score_scale` of `[0, 2]` and `level`, the nearest rubric index 0, 1, or 2.
- Gate gains `next_checks`, a static map from reason codes. Not model prose.
- Error text stays byte-equal in `content[0]`. The code (`auth`, `quota`, `timeout`, `input_too_large`, `invalid_arguments`, `provider`) is `structuredContent.code`.
- An error result also appends a second `content` text block, `JSON.stringify({"code": "<code>"})` with no extra space. The first block stays byte-equal. Success results stay one block. This is divergence `error-code-content-block`: Claude Code 2.1.283 does not pass `structuredContent` into the tool result, so a client that reads only `content` never saw the code.
- Parity fixtures that change are a registered divergence. The expectation adds these fields to the recording. It does not hand-edit the corpus.

## Consequences

- A grep for `verified` can still hit a row whose action is `escalate`. `stands` is the boolean that grep should have been.
- A client that reads only `content[0].text` still does not see the code. A client that reads later content blocks, or `structuredContent`, does.
