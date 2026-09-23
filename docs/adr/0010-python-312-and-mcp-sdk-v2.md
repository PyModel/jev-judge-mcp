---
status: accepted
---
# Python 3.12+ on the MCP Python SDK v2 (`MCPServer`)

We target Python ≥3.12 with `mcp>=2.2,<3` and build on `mcp.server.mcpserver.MCPServer` (verified in the 2.2.0 wheel, 2026-09-21), managed with `uv`. The reference runs on the TS SDK 1.x; v2 is the current Python line and, per its README, still negotiates older protocol revisions — unverified here, so clients that speak `2025-06-18` to the TS server must be proven to keep working. Choosing v1 (`FastMCP`) would mean an early forced migration; choosing <3.12 buys little since `mcp`, `typesafe-sdk` and `regex` all require ≥3.10 anyway.

## Consequences

- Phase 1 must prove the negotiated protocol version and `tools/list` output against the TS snapshot before any tool is ported.
- The `tools/list` diff parses both JSON documents, sorts object keys recursively, and requires exact equality of `name`, `title`, `description`, `inputSchema`, and `execution`. `execution.taskSupport` is `"forbidden"` on every tool, as in the snapshot. Key order is normalized by that sort. A different schema dialect is a bug in the schema we publish.
- `serverInfo.name` is `jev-mcp` (ADR-0009). `serverInfo.version` is this distribution's version. The reference reports its package version, `0.5.0` (`index.ts:74-77`). The initialize comparison ignores `serverInfo.version`.
