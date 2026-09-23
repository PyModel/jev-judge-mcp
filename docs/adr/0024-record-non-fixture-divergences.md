---
status: accepted
---
# Record the non-fixture divergences (N2, N5, T7, T9)

The first divergences the project knew about lived as prose in phase working notes because the
fixture system could not see their surfaces. ADR-0020 gives
every surface a ledger entry; this ADR records the four process/protocol differences as decisions,
so the parity claim per surface is truthful.

- **N2 — initialize capabilities (`initialize-capabilities-unverified`, gap).** The reference's
  initialize response was never captured; mcp 2.2 advertises `prompts`, `resources`, and
  `experimental` alongside `tools`. Python's advertised capability set is pinned by test; the
  comparison against the reference stays open until a capture exists. Registered as a gap, not
  silently assumed equal.
- **N5 — signal exit code (`signal-exit-code`, sanctioned).** The reference (Node, no handler)
  dies by signal. Python installs handlers: SIGINT and SIGTERM stop the loop and exit 0 without a
  traceback, stdio and HTTP alike. Pinned by the stdio integration tests.
- **T7 — missing-arguments error shape (`missing-arguments-error-shape`, sanctioned).** The
  reference hands zod `undefined` and reports one root-level error. The Python SDK delivers `{}`,
  so the reply lists each required field as `Required`. The zod-compatible renderer
  (`tools/arguments.py`) is otherwise exact; the missing-object case is a transport-shape
  difference, pinned by unit test.
- **T9 — lone-surrogate frame dropped (`lone-surrogate-frame-dropped`, sanctioned).** The
  reference accepts a JSON-RPC frame containing a lone surrogate escape; the Python SDK's parse
  path drops the frame with no reply and no log line, before any tool runs, and the server keeps
  serving. This is an SDK surface: the behavior is pinned (`tests/security/test_wire.py`), not
  fixed, and fixing it would mean forking the SDK.

## Consequences

- The per-surface parity statement may now say: tool-call parity complete; wire, arguments,
  lifecycle, and initialize parity known and registered.

## Amendment (2026-09-21): T7 closed, T9 narrowed to Streamable HTTP

- **T7 is no longer a divergence.** `JevMCPServer.call_tool` reads the raw request params: a
  missing `arguments` is validated as `undefined` and reported at the root, as the reference
  reports it, and an explicit `null` is refused before any tool with the reference's -32603
  (`tests/integration/test_tools_stdio.py`). The `missing-arguments-error-shape` entry is removed.
- **T9 is fixed on stdio without forking the SDK.** `src/jev_judge_mcp/stdio.py` replaces the SDK's
  `stdio_server`: frames are read with `JSON.parse` semantics and a lone surrogate is written back
  as the `\udxxx` escape `JSON.stringify` writes (`tests/security/test_wire.py`). Streamable HTTP
  (Tier B, ADR-0021) keeps the SDK's parser, which answers such a frame with -32700 instead of
  dropping it; that remains, as `lone-surrogate-http-parse-error`.
