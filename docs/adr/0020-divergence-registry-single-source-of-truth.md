---
status: accepted
---
# The divergence registry is the single source of divergence truth

ADR-0001 makes every intentional difference from the reference a Sanctioned Divergence, but the
ledger that recorded them was scattered: fixture tags cover tool output only, `tests/parity/divergences.py`
authored its own facts, and the non-fixture divergences (initialize capabilities, wire parsing,
argument-error shape, process lifecycle, transport reliability) lived as prose in `tasks/*.md`. The
phrase "100% parity excluding tagged divergences" therefore claimed more than the ledger could see.

All divergence facts now live in one machine-readable registry, `docs/reference/divergences.json`.
Each entry states, and integrity tests enforce:

- `surface` — closed enum: `tool_schema`, `arguments`, `request`, `provider`, `policy`, `result`,
  `wire`, `initialize`, `lifecycle`, `transport`.
- `tier` — `tier_a` (stdio, the reference-compatible product transport, ADR-0021), `tier_b`
  (streamable-http), or `both`.
- `status` — `sanctioned` (decided, pinned) or `gap` (recorded, undecided).
- `adr` — a path into `docs/adr/` that exists.
- `tests` — pinning tests that exist.
- `fixtures` — fixture-call ids (`<fixture>#<index>`) that replay a Python expectation, plus the
  `fixture_tags` the fixture payloads carry.
- `reference_behavior`, `python_behavior` — the delta in one sentence each, so the registry is
  auditable without opening the ADR.

## Consequences

- `tests/parity/divergences.py` contains zero independently authored divergence facts: it keeps only
  the expectation *functions*, keyed by call id, and builds `DIVERGENT` from the registry. A call id
  in the registry without a function fails at import.
- `tests/contract/test_divergence_registry.py` is the integrity gate: closed enums, unique ids,
  existing ADR and test paths, every tagged fixture call registered exactly once, and `DIVERGENT`
  equal to the registry's fixture set.
- The parity claim is stated per surface ("tool-call parity: complete; wire parity: divergence
  registered; initialize parity: unverified"), never as one boolean.
- A new divergence is a registry entry plus its ADR and pinning test; prose notes in `tasks/` are
  pointers, never the ledger.
