---
status: accepted
---
# Cap values, measurement, and enforcement are three modules

`limits.py` becomes the data owner of every frozen Cap in `parity-manifest.json` `caps`: typed per-tool constants (`TextCap(utf16_units=2000)`, counts, budgets) covering the manifest's three behaviors — reject, truncate, budget-error. It does not measure and does not enforce. UTF-16 measurement stays in `text.py` (ADR-0005). Applying a Cap to a value — and the frozen error texts, including gate's `toLocaleString('en-US')` rendering of 200000 as `200,000` (`index.ts:1397`, exposed as a `serialize.py` helper beside `to_fixed`, which today matches no existing formatting mode) — lives in `validation/caps.py`.

Runtime code never reads `docs/reference/parity-manifest.json`. The manifest is the oracle, not the source: `test_limits_match_parity_manifest` asserts `limits.py` constants equal the manifest values, so a re-freeze diff points at one module.

Caps are layer-1 input validation, not Policy: they never land in `policy/thresholds.py` (ADR-0002's boundary, now stated explicitly). The design review's unadjudicated verify/screen aggregate-input quirk has exactly one legitimate home here: if an aggregate Cap is accepted, it is a Sanctioned Divergence whose constants and error text live in `limits.py`/`validation/caps.py` and whose fixtures carry the divergence tag.

## Considered Options

- **Load the manifest at runtime** — rejected: production behavior must not depend on a file under `docs/`.
- **Per-tool local Cap constants** — rejected: ten transcriptions of frozen data; a manifest change would chase ten files.

## Consequences

- `limits.py` is populated with the first P5 tool, not after; its "Populated in P2/P5" placeholder is discharged by this ADR.
- `serialize.py` gains the locale-grouping helper; nothing else there changes.
- The helper is `js_number_to_locale_string_en_us`: a frozen JS behavior, not an i18n API. No Python `locale` machinery, which would import process locale state into a deterministic rendering. Verified against the pinned Node oracle (v24.19.0): grouping persists beyond 1e21, ties round half-up on the shortest round-trip decimal, and non-finites render as ICU symbols. Tests pin 0, 999, 1000, 200000, negatives, and large safe integers.
- Tests: manifest-equality, plus boundary tests at cap and cap+1 for each of the three behaviors (closing the two unrecorded boundary successes from the parity README).

## Amendment (2026-09-22): cutting records itself; item cuts are telemetry-only

The review found the Truncated Context fact kept by hand in two places. `jev_gate` checks five
fields with `over_doc_cap`/`over_cap` and then cuts the same five with `capped_doc`/`truncate`;
`jev_review` does the same for three. Meanwhile `jev_find`, `jev_rerank` and `jev_classify` cut
candidate and item text without recording it, so the `truncated` metric undercounts against its
own definition (P9 gap G1).

Decision (implemented 2026-09-22):
- `validation/caps.py` gains a per-call `CapLedger`. `text(value, cap, scope)` cuts exactly as
  `truncate` does (a value changes exactly when `length > cap`) and records the cut; `note(scope)`
  records a cut made elsewhere (jev_extract's capped or skipped candidates, a `context` cut).
  `context_cut` is what Policy reads and `scopes` goes to `ToolResult.truncated`. `over_cap`,
  `over_doc_cap` and `capped_doc` go away.
- **Scope decides who reads the record.** A `context` cut (the review and gate documents, gate
  claims and evidence) feeds Policy's never-auto rule, as today. An `item` cut (a candidate's or a
  class's text in find, rerank and classify) feeds telemetry only. The reference returns `auto`
  over cut item text (fixture `truncated/classify-item-and-class-text-over-cap.json`), so wiring
  item cuts into Policy would break parity.
- The metric becomes `truncated{scope="context"|"item"}`.
- `jev_classify`'s budget error text moves out of the tool into `validation/caps.py`, rendered with
  `js_number_to_locale_string_en_us`. The output is identical today: that helper and Python `:,`
  agree for every integer tested up to 2^53−1.
- The truncate calls that cannot fire because the schema rejects first (`jev_compare`, extract's
  `document`) stay, routed through the ledger.
