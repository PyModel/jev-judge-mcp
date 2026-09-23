---
status: accepted
---
# Behavior-compatible rewrite against a frozen TypeScript reference

The Python server is a rewrite of the TypeScript reference server 0.5.0 (commit `69ffb4b`, tree `aa89f96`), not a transliteration of `index.ts`. The spec is the reference's observable behavior — `tools/list` schemas, defaults, caps, output payloads, fail-closed outcomes — captured in `docs/reference/parity-manifest.json` and `ts-0.5.0-tools-list.json`. Internal structure is free to differ. Parity is judged on parsed JSON output under identical simulated provider answers, never on live model output. Any intentional difference is a **Sanctioned Divergence** and needs its own ADR; the reference's accidental-looking behaviors are listed as `known_reference_quirks` and default to *keep* until an ADR says otherwise.

## Consequences

- A newer upstream release does not change the target. Re-freezing is a deliberate act that regenerates both reference files and re-runs the parity suite.
- The reference's 113 tests (27 unit, 60 mock, 8 provider, 18 live e2e) are the starting contract corpus, not the ceiling.
