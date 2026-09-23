---
status: accepted
---
# Publish as `jev-judge-mcp`

Supersedes the naming decision in ADR-0009. The repository is `PyModel/jev-judge-mcp`, and the
distribution, console script, and installer launch argument are `jev-judge-mcp`; the import package
is `jev_judge_mcp`. The name was free on PyPI when chosen. `jev-mcp-python` 0.1.0 on PyPI was an
unofficial upload and is deleted; there is no redirect package.

The reasons in ADR-0009 still hold: the PyPI name `jev-mcp` belongs to an unrelated project, so the
docs never say `uvx jev-mcp` and the installer refuses to write it.

## Consequences

- Names the TypeScript reference pins stay: `serverInfo.name` `jev-mcp`, the tool names and
  schemas, and the OpenRouter `X-Title` headers.
- On-disk and config names stay: the `jev` entry key the installer writes, the `JEV_*` and
  `JEV_MCP_*` variables, and the `jev-mcp` key, cache, and state directories.
- Renaming again after 1.0 would break every user's MCP config.
