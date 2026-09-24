---
status: accepted
---
# Keep port 8000, and exit cleanly when it is taken

The default `JEV_MCP_HTTP_PORT` is 8000. Dogfood hit it because a local model server already
listened there. The reference does not fix a port: it is stdio-only (ADR-0021), and Streamable
HTTP is a Python tier-B transport. There is no reference value to align to.

## Decision

Keep 8000. It is the published default. Changing it would surprise every config and doc that omits
the variable and expects the port the README already names. The collision is an operator problem
with a fail-closed answer, not a reason to move the default.

A Streamable HTTP start whose port is already in use exits 1 before logging and before the server
listens. The process prints one line and nothing else:

`JEV_MCP_HTTP_PORT=<port> is already in use; set JEV_MCP_HTTP_PORT to a free port`

No traceback, no listener, no extract worker. stdio does not bind, so it is unchanged. A host that
does not resolve is not this gate: that failure stays the server's, as before.

The README says 8000 is often already taken, next to the HTTP transport notes.

## Consequences

- An operator who finds 8000 taken sets `JEV_MCP_HTTP_PORT` and restarts. The message names the
  variable and the port.
- The default in `settings.py` stays 8000. This is not a wire divergence: the reference has no HTTP
  port, and a refused start puts nothing on the wire.
