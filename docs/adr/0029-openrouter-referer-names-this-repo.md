---
status: accepted
---
# The OpenRouter HTTP-Referer names this repository

OpenRouter reads `HTTP-Referer` for attribution. The reference server sends the URL of its own
repository; carrying that URL here would attribute this server's traffic to a repo it does not
live in and name a project this distribution is not part of. This server sends
`https://github.com/PyModel/jev-judge-mcp`, its own repository URL. `X-Title` and `X-OpenRouter-Title`
stay `jev-mcp`, as in the reference.

## Consequences

- The only wire difference is the referer URL; no header is added, dropped, or reordered.
- Recorded in `docs/reference/divergences.json` as `openrouter-referer`, pinned by
  `tests/contract/test_providers.py`.
