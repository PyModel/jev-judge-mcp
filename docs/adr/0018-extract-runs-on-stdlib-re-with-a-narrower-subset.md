---
status: accepted
---
# jev_extract matches with stdlib `re`, and the subset is narrower than ADR-0004's list

ADR-0004 names the `regex` module as the matcher behind the dialect gate. The implementation uses
stdlib `re` instead, and the P5 build found the reason in the two untagged `regex-timeout/`
fixtures: `regex` finishes catastrophic patterns that V8 cannot. On `"a"*40 + "!"`, V8 times out
on `(a+)+$`, `^(a+)+$`, `(a|a)*b`, `(\w+\s?)*$`, and `(a| aa)+$`-class alternations; `regex` returns
candidates for four of them at once, which would turn recorded `invalid_pattern` results into
`not_found`. `re`'s backtracker stalls like V8's, so the kill at the deadline produces the
reference's result. ASCII ignore-case is `re.ASCII | re.IGNORECASE`, and U+212A does not fold to
`k` under it, as ADR-0004 requires. The worker's SIGALRM default action stays the second line of
defence, since `re` never yields mid-match. ADR-0004's engine wording reads as amended by this ADR;
the `regex` dependency is removed from `pyproject.toml`.

The implemented subset is also narrower than ADR-0004's enumeration — each cut is `invalid_pattern`
with a named reason, found by the Node differential test or taken to stay on the safe side
(`extract-stdlib-re-subset` in `docs/reference/divergences.json`): a variable repeat of a group that can match empty; backreferences
and legacy octal escapes; alphanumeric identity escapes; `\c` without a letter; incomplete
`\x`/`\u`; quantified lookarounds; lookarounds inside a lookbehind; a class range with a
class-escape endpoint; quantifier bounds above 2³¹−1; nesting deep enough to exhaust recursion.
Lookbehind must be one fixed width across its alternatives. The fixture-visible reason texts are
pinned in `tests/parity/divergences.py`; flags V8 itself rejects keep V8's text. Widening the
subset requires `tests/parity/test_extract_differential.py` (Node) green.

## Consequences

- `regex` is gone from the dependency tree; nothing imports it.
- Patterns outside the narrower subset get `invalid_pattern` with a named reason — a divergence
  already tagged where a fixture shows it.
- ADR-0016's deadline model governs the pool; this ADR governs only the engine and the subset.
