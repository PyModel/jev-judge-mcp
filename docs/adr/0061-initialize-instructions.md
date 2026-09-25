---
status: accepted
---
# Initialize instructions are a registered divergence

A client that defers tool schemas never sees `tools/list`. The MCP `initialize` result is the text it does load. This server used to send no `instructions`.

## Decision

- `JevMCPServer` passes one `instructions` string, built from the tool registry so each published name appears once.
- The string says to call `jev_gate` before claiming done on a diff and before opening or merging a pull request, and to honor `action` rather than grep `verdict`. `auto` may proceed. `review` still belongs to the caller. `escalate` means stop; this server does not call another model.
- The string has no threshold numbers, no secrets, and no harness-specific names.
- This is a non-fixture divergence. The reference initialize response was never captured (`initialize-capabilities-unverified`). Tool payloads stay frozen except where a later ADR says otherwise.

## Consequences

- `tests/contract/test_tools_list.py` pins the initialize result.
- A client that already injects its own routing text will see both. The server string stays short.
