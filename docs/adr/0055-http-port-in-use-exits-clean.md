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

The probe sets `SO_REUSEADDR`, the same option uvicorn sets before its POSIX bind, and keeps
`IPV6_V6ONLY` on an IPv6 socket. After a stop, the server side of a connection can sit in
`TIME_WAIT` for tens of seconds. A bind without `SO_REUSEADDR` reports that as `EADDRINUSE`, so a
restart would exit "already in use" even though uvicorn could bind. With the option set on the
previous server and on the probe — the realistic restart, since uvicorn sets it — `TIME_WAIT` is
not taken; Linux keeps the entry's flag from the socket that made it, so an entry left by a bare
socket blocks the probe exactly as it blocks uvicorn's own bind, while macOS consults only the new
socket. A socket that is still listening is still taken, and the process exits with the one line.

The probe is not the listen. It closes the socket and returns, then uvicorn binds. If another
process takes the port in that gap, the gate has already passed. The operator sees the identity
log line, uvicorn's "Started server process" line, and uvicorn's own bind error (the `OSError` it
logs, then `sys.exit(3)`), not the one-line `JEV_MCP_HTTP_PORT` message. The gate does not retry
that later failure. The cases it decides are a port held at start, and a port left in `TIME_WAIT`
by a previous process.

## Consequences

- An operator who omits the variable gets 8088. One who finds that taken sets `JEV_MCP_HTTP_PORT`.
- A port held for the whole budget still refuses. A port that frees during the budget is used.
- The README says 8000 is often taken, and that a taken configured port is retried briefly and then
  refused by name.
