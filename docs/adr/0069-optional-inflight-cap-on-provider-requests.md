---
status: accepted
---

# An optional cap on concurrent provider requests

## Context

A burst of concurrent tool calls fans out one provider request each, and every request carries
its own bounded retries (ADR-0057): 64 concurrent calls can mean 64 upstream requests in flight.
The extract pool has a queue bound; the provider path had no local admission control, and the
load gate measures a stubbed provider, so the unbounded fan-out against a real upstream was
never observed, only implied.

## Decision

`JEV_MCP_MAX_INFLIGHT` (default `0`, off — parity with the reference) caps concurrent provider
`evaluate` calls per process, with a semaphore in `Runtime.ask`. Calls beyond the cap wait; they
hold no provider resource and stay cancellable by the client's MCP cancellation, exactly like
any other wait. A cache hit makes no provider request and never takes the semaphore.

The cap is local only. The retry owner (ADR-0057) and the no-whole-call-deadline property
(ADR-0011) are unchanged; a queued call simply starts later. stdio (tier A) and Streamable HTTP
(tier B) share the one process-wide knob.

## Considered options

- **A bounded queue that rejects** (like the extract pool's saturation reason) — rejected: a
  tool call is not sheddable. The client is waiting for exactly this judgment; waiting preserves
  the answer where refusing would fail the call.
- **Per-tool budgets** — rejected: the burst problem is process-wide, and the reference has no
  per-tool concurrency behavior to mirror.

## Consequences

- Default off: with the knob unset, behavior is byte-identical. Registered divergence:
  `provider-inflight-cap` in `docs/reference/divergences.json`.
- With the knob set, upstream concurrency is bounded and latency of queued calls grows by their
  wait; there is no queue-length limit of its own.
- The semaphore is created lazily inside the running loop, so `Runtime` stays constructible
  outside one.
