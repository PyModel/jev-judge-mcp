---
status: accepted
---
# Vercel AI Gateway keeps its resolution slot but is unsupported in Python 1.0

The reference auto-resolves providers in the order typesafe → openrouter → cloudflare → **vercel** → compatible, and reaches Jev on Vercel only through the AI SDK's `experimental_evaluate()`, which has no documented Python equivalent. Python keeps the slot: `JEV_PROVIDER=vercel`, or auto-resolution reaching `AI_GATEWAY_API_KEY`, raises a clear "vercel provider is not supported by the Python server; use typesafe, openrouter, cloudflare or compatible" error. Silently skipping to `compatible` would route a user's traffic somewhere they did not configure, and emulating Jev over chat completions would change the judgment semantics while claiming parity.

## Consequences

- A user with only `AI_GATEWAY_API_KEY` gets a startup-time error instead of a working server — a Sanctioned Divergence, visible and documented in the README.
- Adding Vercel later is additive once a documented Python evaluation contract exists.
