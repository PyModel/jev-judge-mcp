---
status: accepted
---
# Caps and truncation count UTF-16 code units, not Python characters

Every character cap in the reference is a JS `.length`, which counts UTF-16 code units; Python's `len()` counts code points, so any text with emoji or other astral characters would hit caps and truncation points at different places. All cap checks and `truncate()` go through one helper that measures and slices in UTF-16 units. When a cut would split a surrogate pair, Python drops the dangling high surrogate instead of emitting a lone surrogate (quirk Q8) — a Sanctioned Divergence, because a lone surrogate is not valid UTF-8 and breaks downstream JSON consumers.

## Consequences

- Plain `len(text) > MAX_…` anywhere in `src/` is a bug; lint for it.
- Truncated text can be one code unit shorter than the reference in the split-pair case; fixtures cover it.
- This drop applies to `truncate()` only. A regex match that is one UTF-16 unit is a candidate value and is kept (ADR-0004); the serializer escapes it (ADR-0006).
