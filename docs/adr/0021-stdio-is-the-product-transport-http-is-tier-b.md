---
status: accepted
---
# stdio is the product transport; streamable-http is Tier B experimental for 1.0

The Python server has two transports, but only one of them replaces the reference. stdio is
**Tier A**: the reference-compatible product surface, governed by parity (ADR-0001) and the
fixture corpus. `streamable-http` is **Tier B**: a Python-only extension with no reference
counterpart, experimental for 1.0. It binds localhost by default (`JEV_MCP_HTTP_HOST`, ADR-0008
process config), carries no reliability contract — no provider deadline, no admission control, no
load-tested limits — and must not be promoted to a supported service before P9 evidence exists:
load measurements at the published budgets, observability, and explicit runtime limits
(env-only settings per ADR-0008, never `limits.py`, which owns frozen reference Caps).

The tier classification also settles the reliability question the reference never had to answer:
**Tier A keeps the reference's no-deadline behavior.** `Runtime.ask` passes `timeout=None`, the
client's own MCP cancellation (ADR-0011) is the recovery path, and the property is registered
(`stdio-no-provider-deadline`). Adding a deadline to stdio would be divergence without a threat;
a single-client surface whose client can cancel does not need one.

## Consequences

- Registry entries carry `tier`, so every divergence states which transport it belongs to.
- HTTP-specific hardening (provider deadline, concurrency/admission limits, busy semantics) moves
  out of the pre-P9 queue and becomes P9 evidence-driven work, gated on the promotion decision.
- Documentation and release gates say "experimental" for Tier B, and parity claims are never
  extended to cover it.
