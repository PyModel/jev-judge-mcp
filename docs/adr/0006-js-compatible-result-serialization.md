---
status: accepted
---
# Tool results are serialized to match JSON.stringify(payload, null, 2)

Every reference tool returns a single text block whose body is `JSON.stringify(payload, null, 2)` (`index.ts:86`); agents and scripts parse that text, and some pin on its exact shape. Python's `json.dumps` differs in number formatting (`1.0` vs `1`, `1e-07` vs `1e-7`), separators, and escaping. We use one serializer that preserves payload key insertion order, 2-space indent, and JS number formatting per ECMAScript Number::toString: shortest round-trip digits, integral floats as integers, exponential notation only when the decimal exponent is ≥ 21 or ≤ −7, written `e+21` / `e-7` with no zero padding. Python's `repr` switches to exponential below 1e-4 (`3.2e-05` where JS prints `0.000032`), and sub-1e-4 probabilities are routine in large Choice distributions, so this is the common case, not an edge case (quirk Q7). Reason strings that use `toFixed(2)` go through a helper reproducing JS round-half-up on the exact binary value (quirk Q6: `0.125 → "0.13"`, where Python gives `"0.12"`; `lib.ts:79-85`).

Scalar values are written as UTF-8 characters, which is what `ensure_ascii=False` is for: `JSON.stringify` of U+1F600 is the character, not `\uD83D\uDE00`. Unpaired surrogates are escaped as `\uXXXX`. `JSON.stringify("\uD83D")` is the six-character escape. Leaving the raw surrogate in a Python string makes the UTF-8 encode of the MCP frame raise `UnicodeEncodeError`. Regex matches that are one UTF-16 unit hit this path (ADR-0004).

## Consequences

- The parity suite compares parsed JSON for semantic equality **and** raw text for byte equality; byte mismatches outside documented divergences fail CI.
- The differential generator includes `-0.0` (`JSON.stringify(-0)` is `0`), subnormals, the `1e21` and `1e-7` boundaries, and sums of the `0.1 + 0.2` class, along with the ordinary 10k payloads.
- MCP `structuredContent`/output schemas are not added in 1.0 — the reference has none, and adding them changes what clients see.
