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

## Live evidence (2026-09-24)

Six `jev_compare` calls through the server, using its own key discovery, model `jev-latest`. Cap
was 10. Stopped when a response carried `warnings`. No credential was recorded. The parity corpus
is untouched: the triggering answers are replayed from
`tests/fixtures/compare/live-humidity-aspect-contradiction.json`, which is not a reference fixture.
Usage from the live call was not retained; that envelope omits it.

| attempt | what differed | overall | aspect that was meant to disagree | that aspect | warning |
|---|---|---|---|---|---|
| five-of-six-office | release office Dublin vs Cork | contradicts | office | contradicts | no |
| footnote-page | footnote page 12 vs 13 | contradicts | footnote page | contradicts | no |
| example-port | example port 8000 vs 8080 | contradicts | example port | contradicts | no |
| middle-initial | one passage restates the initial, the other does not | same_fact | middle initial | different_facts | no |
| patch-digit | build 1841 vs 1842 | contradicts | build number | contradicts | no |
| humidity-point | humidity 40 percent vs 41 percent | same_fact | humidity | contradicts | yes |

On the first five, the overall either followed the contradiction or the aspect did not contradict,
which is the dogfood result. The sixth is the missing live case: city, date, high, and wind were
`same_fact`, humidity was `contradicts` at 0.95, and the overall stayed `same_fact` (0.56, margin
0.13, decision `review`). The warning was `Aspect "humidity" reports contradicts while the overall
relation does not; inspect before acting`. The contract test replays that answer and asserts the
warning.
