---
status: accepted
---
# Extract pool saturation has its own reason, distinct from the regex timeout

ADR-0016 made the request deadline absolute across queue admission and execution, and sanctioned
the resulting divergence: under a saturated pool, a pattern the reference would have executed can
fail without running. But both failure mechanisms — *the pattern ran out of time* and *the pool
refused admission before spending any time* — collapsed into the regex timeout's text, which tells
the agent to "simplify the pattern". That is the wrong remediation for capacity pressure: the
pattern is fine, and simplifying it changes nothing while retrying makes the saturation worse.

A queue-bound admission refusal now reports the stable reason token `regex_pool_saturated`, with
no timeout figure and no advice about the pattern. Deadline expiry — including a pattern that
could not be admitted *within its deadline* — keeps the reference's timeout text. The two
mechanisms are observable distinctions, not spellings: the contract suite pins both, and the
`mcp.tool` span labels them differently (`saturated` vs `timeout`).

## Consequences

- The process executor answers the queue bound with its own `Saturated` result;
  `extract/candidates.py` maps it and `Timeout` to their own reason strings (executors carry no
  reason text).
- The saturated path is a Sanctioned Divergence with no fixture (no fixture exercises
  saturation), registered as `extract-pool-saturation-reason`.
- P9 load measurements can count the two failure classes separately.
