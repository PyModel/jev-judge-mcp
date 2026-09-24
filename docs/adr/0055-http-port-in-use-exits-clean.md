---
status: accepted
---
# Default the HTTP port to 8088, and exit cleanly when it stays taken

The reference does not fix a port. It is stdio-only (ADR-0021); Streamable HTTP is a Python tier-B
transport. Dogfood hit the old default, 8000, because a local model server already listened there.

## Decision

The default `JEV_MCP_HTTP_PORT` is 8088. 8000 is a common local port, so it is the wrong default.
This is not a wire divergence: the reference has no HTTP port, and a refused start puts nothing on
the wire.

A Streamable HTTP start binds the configured port, not a fallback. If that bind raises
`EADDRINUSE`, the same port is tried 4 times and the waits stay inside 2 seconds. Then the process
exits 1, before logging and before the server listens, with one line and nothing else:

`JEV_MCP_HTTP_PORT=<port> is already in use; set JEV_MCP_HTTP_PORT to a free port`

No traceback, no listener, no extract worker, and no silent move to another port. Any other bind
error fails on the first try. stdio does not bind. A host that does not resolve is not this gate.

The waits are `time.sleep` and `time.monotonic` unless a test injects them. There is no retry
environment variable: the budget is part of the startup contract, not operator configuration.
Upstream Jev calls are a different retry owner and are not part of this gate.

## Consequences

- An operator who omits the variable gets 8088. One who finds that taken sets `JEV_MCP_HTTP_PORT`.
- A port held for the whole budget still refuses. A port that frees during the budget is used.
- The README says 8000 is often taken, and that a taken configured port is retried briefly and then
  refused by name.
