---
status: accepted
---
# jev_extract accepts a restricted ECMAScript subset, matched in UTF-16 units, in a killable process pool

`jev_extract`'s `pattern` is JavaScript regex source. The reference compiles it with `new RegExp` and runs `document.matchAll` in a worker thread killed at 1,000 ms (`index.ts:890-940`, commit `69ffb4b`). Without the `u` flag, V8 matches UTF-16 code units. Python `str` and the `regex` module match Unicode code points. `.{3}` matches a document of one BMP character plus U+1F600 in V8 (three units) and matches nothing in Python (two code points). Translating classes and anchors leaves that gap in place.

The subset we accept is translated and matched in unit space:

1. Each UTF-16 code unit becomes one Python character with the same numeric value: encode UTF-16-LE, then `chr` of each 16-bit unit. Map match text back with `utf-16-le` and `surrogatepass`. A match may be one unpaired surrogate; ADR-0006 escapes it. Strict UTF-16 decoding raises on that match.
2. `.` outside a character class is one unit that is not a line terminator (U+000A, U+000D, U+2028, U+2029). Quantifiers and `[^x]` then see units, which is what makes astral documents agree with V8.
3. `\d`, `\w`, and `\b` use the ASCII definitions (`[0-9]`, `[A-Za-z0-9_]`, and the boundary of that set). On Node 24 these stay ASCII even with `u` (U+0660 and U+FF10 do not match `\d` or `\w`). `\s` is ECMAScript WhiteSpace plus LineTerminator: U+0009, U+000A, U+000B, U+000C, U+000D, U+0020, U+00A0, U+1680, U+2000 through U+200A, U+2028, U+2029, U+202F, U+205F, U+3000, U+FEFF. U+0085 and U+180E are excluded. An ASCII class misses U+00A0; Python `\s` includes U+0085.
4. `^` and `$` are start and end of the string (`\A` and `\Z`). On Node 24, `/a$/` does not match before a trailing newline.
5. `invalid_pattern` rejects `u` (it switches the matcher to code points), `y`, `d`, `v`, `s` (`.` would match line terminators), `m` (`$` would match before U+000A, U+000D, U+2028, and U+2029, while Python multiline `$` matches before U+000A only), `i` when any literal or class endpoint is outside U+0000–U+007F, and any other syntax outside the subset (variable-length lookbehind, `\u{...}`, property escapes, named groups). ASCII-only `i` is pinned with `regex` ASCII ignore-case. `regex` VERSION0 simple case folding folds U+212A to `k`; V8 without `u` does not (`/\u212A/i` against `k` is false on Node 24.19.0), and VERSION1 full folding also matches `ß` to `ss`, which V8 does not. No `regex` flag is that canonicalization. `/é/i` matches `É` in V8, so ASCII-only `i` is a subset cut.
6. The reference's invalid-pattern text is the worker's `SyntaxError.message` (`index.ts:906-908`). On Node 24.19.0, `(?/` produces `Invalid regular expression: /(?//: Invalid group`. Python returns a stable named reason. The timeout text stays verbatim: `regex timed out after 1000ms; simplify the pattern`.

Matching runs in a pool of worker processes, size `min(8, os.cpu_count() or 1)`. A slot runs one pattern. On deadline that slot's process is killed and replaced. The `regex` module timeout is a second line of defence, not the guarantee. Inside one `jev_extract` call, fields run sequentially in caller order (ADR-0012); duration is not part of the output. A full pool queues. A cancelled call (ADR-0011) kills only the process running that call and drops that call's queued patterns.

Candidate equality is proved by the differential fuzz suite: in-subset patterns over BMP text, astral text (U+1F600 beside a BMP character), unpaired-surrogate matches, and `\s` against U+00A0 and U+0085, run through Node 24 and Python. The lists must be identical.

## Considered Options

- **Run patterns with `regex` as-is and document the dialect change** — rejected: callers written against the TS server would get different candidates with no error, breaking `value ∈ regex_matches(document)` as the caller understands it.
- **Embed a JS engine (QuickJS binding) or shell out to Node** — exact parity, but adds a native or Node dependency to a Python package and a second sandbox to secure. Revisit if the subset proves too small in practice.
- **Thread + `regex` timeout only** — rejected: the `regex` timeout is checked cooperatively inside the matcher and a Python thread cannot be forcibly stopped; only a process kill is a hard bound.
- **Latin-1-decode the UTF-16-LE bytes** — rejected: that is two characters per unit (`"a"` plus U+1F600 is 6 latin-1 characters and 3 units), so `.` matches half a unit.
- **One shared worker process** — rejected: P6 and P9 run 64 concurrent calls, and one process would serialize every field of every call.

## Consequences

- Patterns outside the subset are a Sanctioned Divergence: the reference accepts them, Python returns `invalid_pattern` with a named reason. Tag `divergence:ADR-0004`. The invalid-pattern fixture locks that reason. V8's message is not the target.
- The match pipeline order is frozen: drop zero-length, dedup, skip overlong (count `tooLong`), cap at 20 (`truncated`).
- Queueing changes latency only. P9 measures it. It must not change tool output.
- A regex match that is one UTF-16 unit is a candidate and is kept. ADR-0005 drops a dangling surrogate only when `truncate()` cuts one.
