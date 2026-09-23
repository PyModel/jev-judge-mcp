---
status: superseded by ADR-0049
---
# Publish as `jev-mcp-python`, never document `uvx jev-mcp`

The PyPI name `jev-mcp` is already taken by an unrelated third-party project (0.1.2, Aitejiu/jev-harness-lab). Documenting `uvx jev-mcp` would make users install and run someone else's code with their provider keys in the environment. The distribution and console script are `jev-mcp-python`; the import package is `jev_mcp`. The MCP `serverInfo.name` stays `jev-mcp` so clients see one logical server across implementations. Likewise the TypeSafe SDK is `typesafe-sdk` (TypeSafe AI); the similarly named `typesafe` package is unrelated.

## Consequences

- Migration docs swap the TypeScript reference server command for `uvx jev-mcp-python`.
- Renaming after 1.0 would break every user's MCP config, so this is effectively permanent.
