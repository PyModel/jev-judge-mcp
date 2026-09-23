---
status: accepted
---
# An optional response cache replays identical requests verbatim

`JEV_MCP_CACHE` (truthy) turns on a provider-response cache; the default is off and the off path
is a no-op. An identical request — same provider, model, state, and questions, in the same key
order — replays the recorded `Evaluation` from disk instead of calling the provider, at zero API
cost.

## Decision

- **Key.** SHA-256 of the exact request body under `JSON.stringify` semantics (`stringify_compact`
  of `{provider, model, state, questions}`): one character of difference, a reordered question
  map, or another model slug is another entry. The key is computed from the wire form, so the
  cache cannot conflate questions that serialize differently.
- **Storage.** One JSON file per entry under `$JEV_MCP_CACHE_DIR`, else
  `$XDG_CACHE_HOME/jev-mcp`, else `~/.cache/jev-mcp`. Entries are mode 0600 inside a 0700
  directory (`jev_judge_mcp/fsutil.py`): an entry holds the judged State, so it is protected at least
  like the stored API key. Writes are atomic (temp + `os.replace`); a
  directory that cannot be created or written means "not cached", never an error. Unbounded: no
  eviction in 1.0 — delete the directory to clear it.
- **Replay is verbatim.** The recorded answers, usage, provider, and model come back as one
  `Evaluation`, so every tool payload built from it is byte-identical to the first answer's. The
  cache never edits a response, never merges, and never synthesizes usage: a hit is the same
  judgment the provider already gave. Tool outputs stay inside the parity contract.
- **Where it sits.** `Runtime.ask`, after provider resolution and before `evaluate`: the cache is
  provider access policy, not a provider (the resolved provider's name keys the entry, and its
  lifecycle is untouched). A hit records a `cache=hit` attribute on the `jev.evaluate` span — and
  no token attributes, because nothing was billed for the replay; the replayed payload's `usage`
  stays verbatim — and logs to stderr; stdout stays protocol-only.
- **Validation on read.** A corrupt, foreign, or non-numeric record is a miss: the loader checks
  the provider name matches, the answers are an object, and both usage counts are finite
  non-negative numbers (`bool` is not a number). No cached record can inject a malformed answer
  the live path would have rejected — the tool's validators run on replayed answers exactly as on
  live ones.

## Considered Options

- **A caching provider decorator** — rejected: it must shadow `name`/`label` per instance, and the
  wrapper would appear in spans and error text as something it is not.
- **An in-process LRU only** — rejected: a server restart would forget everything, and the
  cross-session reuse (the actual saving) is the point.
- **On by default** — rejected: freshness of repeated judgments is the safer default; the tools'
  own guidance already says one call per unchanged decision, and the cache must not make
  accidental repeats invisible in usage accounting (a hit reports the recorded usage, so cost
  telemetry stays honest).

## Consequences

- Env-only configuration (ADR-0008); the flag and directory join `Settings`, and neither name is
  credential-shaped, so the naming guard is untouched.
- The stdio no-provider-deadline property is unaffected: a hit returns before `evaluate`; a miss
  calls it exactly as before with `timeout=None`.
- Registered divergence: `response-cache` in `docs/reference/divergences.json`.
