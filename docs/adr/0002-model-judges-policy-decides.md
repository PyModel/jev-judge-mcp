---
status: accepted
---
# The model judges; deterministic Python policy decides

Every tool is split into four layers with one-way dependencies: tool input validation builds `state` + questions → a **Provider** returns raw answers → strict per-tool **Answer Validation** → a pure **Policy** module maps validated judgments to actions. Providers never interpret answers; policy never performs I/O. We chose this over letting each tool mix HTTP, parsing and thresholds (as `index.ts` largely does) because the policy layer is where fail-open bugs live, and isolating it makes 100% branch coverage and property testing practical.

## Consequences

- Thresholds, weights, margins and reason codes live only in `policy/`; a tool that hardcodes one is a bug.
- A new provider can never change an action — only the answers it hands to validation.

## Amendment (2026-09-22): extract's field outcome moves into `policy/`

The 2026-09-22 architecture review found `jev_extract` deciding its per-field outcome in the tool
module. `tools/extract.py` `_result` hardcodes five reason codes (`no_regex_matches`,
`matches_too_long`, `candidate_limit`, `none_matched`, `none_matched_ambiguous`) and demotes an
incomplete candidate universe to `review` inline. That code sits outside both the 100% branch gate
(`make policy-coverage` measures `jev_judge_mcp.policy` only) and the policy fixture recompute (`TOOLS`
omits `jev_extract`). It is a violation of the consequence above, not a new rule.

Decision (landed):
- A pure `policy/extract.py` owns the outcome:
  `decide_extract_field(evidence: ExtractFieldEvidence, *, threshold, margin)
  -> ExtractFieldDecision(status, reason)`. `ExtractFieldEvidence.judgment is None` means no
  candidate matched, so nothing was asked; such a field is judged on `too_long` alone, since
  `truncated` implies candidates.
- `EXTRACT_REASON_CODES` is the one tuple of extract reason codes. The pin is
  `tests/contract/test_limits.py` `test_extract_reason_codes_match_parity_manifest`, which requires
  it to equal `parity-manifest.json` `policy.extract_reasons` in order.
- Answer Validation stays where it is: `validate_extract_choice`, including ADR-0012 Q2's own
  `<= 0.01` sum tolerance, runs before `ExtractJudgment` is built.
- `invalid_pattern` rows are input failures, not policy. Their reason carries prose from
  `PatternRejected` or ADR-0025, and they are exempt from the reason-code invariant.
- The tool keeps the payload literal, so output stays byte-identical. The tool fixture replay is
  the proof, with no fixture re-recorded.
- `tools/observed.py` traces the new function, because every `policy.__all__` name must be traced.
  The extract `jev.policy` span loses its `action` attribute.
- `jev_extract` joins the policy fixture recompute. `jev_compare` and `jev_decide`, which are
  missing only because `TOOLS` was frozen at P3, follow as a separate change.
- `tools/screen.py`'s invalid path, which hardcodes `review` plus a reason string, is the other
  leak the review found. It moves behind a policy helper in the same stream.
