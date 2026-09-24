---
status: accepted
---
# Compare warns when an aspect contradicts the overall relation

`jev_compare` asks one overall question and one per supplied aspect, and judges each answer
independently (`index.ts:790-879`). The reference never connects the two: an aspect that reports
`contradicts` under a `same_fact` or `different_facts` overall changes nothing in the output —
`compare.py`'s own comment reads "the aspects are not the headline". A caller reconciling two
passages per aspect can therefore get `price: contradicts` beside an overall `same_fact` with no
signal that the rows disagree.

The overall stays the headline: Python does not recompute it, flip it, or gate the action on the
aspects — that would be a behavioral rewrite of the reference's judgment, not a hygiene fix.
Instead, when at least one aspect reports `contradicts` while the overall relation is not
`contradicts`, the tool adds a `warnings` field in `jev_decide`'s style:

- one aspect: `Aspect "price" reports contradicts while the overall relation does not; inspect before acting`
- several: `Aspects "price", "date" report contradicts while the overall relation does not; inspect before acting`

The field is emitted only when a warning exists. Every output without an aspect contradiction —
including every fixture in the recorded corpus — stays byte-identical to the reference.

## Consequences

- One divergence entry (`compare-aspect-contradiction-warning`, surface `result`) and this ADR own
  the change; the pin is `tests/contract/test_compare_warnings.py`.
- The recorded corpus has no compare fixture with an aspect contradiction under a non-`contradicts`
  overall, so no fixture is tagged and no expectation is needed: the parity replay proves the
  unaffected outputs byte-identical.
- The policy-fixture recompute (`tests/parity/test_policy_fixtures.py::check_compare`) reads only
  `overall`, `aspects`, and `thresholds`, so it passes unchanged; the warning is tool-layer prose,
  never policy.
- A fail-closed overall (`relation: null`, `status: invalid_response`) still warns when an aspect
  contradicts: the headline row already says the overall judgment failed, and the warning adds the
  aspect disagreement on top.
