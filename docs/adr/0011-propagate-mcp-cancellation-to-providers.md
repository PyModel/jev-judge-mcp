---
status: accepted
---
# Propagate MCP client cancellation to the provider request

The reference never passes an abort signal from the tool layer to the provider (`askJev()` in `index.ts` drops it, quirk Q10), so a cancelled MCP call keeps its Jev request running to completion. Python propagates cancellation: an MCP `notifications/cancelled` cancels the tool task, which cancels the in-flight `httpx`/SDK request and kills any running regex worker. This is a Sanctioned Divergence: it changes no tool output, only whether abandoned work keeps consuming provider quota and a concurrency slot.

## Consequences

- Cancellation tests are Python-only contract tests, not parity fixtures.
- Nothing a cancelled call started may write to stdout after cancellation.
- The regex worker that gets killed is the pool slot running this call (ADR-0004). Other calls' slots keep running, and this call's queued patterns are dropped.
