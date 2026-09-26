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

## Amendment (2026-09-25)

`jev_find`, `jev_verify`, and `jev_gate` sanitize and de-duplicate caller ids through
`ensure_unique_ids`, and their schemas invite file paths as ids, but no response said which sent
id became which returned id, so a caller could not map `providers_base.py` back to
`providers/base.py`. These three tools append one more additive field, `renamed_ids`: an object
of sent id → returned id, present only when at least one id changed, appended after the body's
last key exactly like the fields above. The sanitization itself is untouched, so id-level parity
with the reference holds; the field is part of divergence `program-response-fields`. A caller id
that collides with gate's implicit `diff` or `tests` evidence does not enter the map: the caller's
id is unchanged and the implicit item is the one suffixed.

## Consequences

- A grep for `verified` can still hit a row whose action is `escalate`. `stands` is the boolean that grep should have been.
- A client that reads only `content[0].text` still does not see the code. A client that reads later content blocks, or `structuredContent`, does.
- A client that ignores `renamed_ids` keeps the old behavior: the returned ids are unchanged. A client that sent path-shaped or duplicate ids reads the map to match results back to its own ids. A guard test fails any tool module that calls `ensure_unique_ids` without surfacing the renames.

## Amendment (2026-09-25): duplicate sent ids — the first occurrence keeps the sent id

When one id is sent on several items, `ensure_unique_ids` keeps the sent id on the first
occurrence in caller order and suffixes later occurrences `_1`, `_2`, … — the reference's own
de-duplication, unchanged. A row's unsuffixed `evidence_ids` entry (or `top[].id`, or a
returned evidence id) therefore names the **first physical item** sent under that id;
`renamed_ids` maps the one sent id to the returned id of its last renamed occurrence (two
duplicates: `{"diff": "diff_1"}` names the second item; three: `{"diff": "diff_2"}` names the
third, later renames overwriting earlier ones, because the map is keyed by the sent id). The
field remains additive and the behavior is unchanged; the harness pages state the rule so a
caller colliding ids can read which physical item a row cites.
