---
status: accepted
---
# extract: the dialect translates; executors execute and own time

`extract/dialect.py` validates pattern syntax and flags, rewrites the dialect, and fixes anchor and case-fold semantics (ADR-0004). It returns a frozen `Translated`. Compilation and matching of caller patterns belong to the execution adapters — an in-process adapter for the differential fuzz suite and the process-pool adapter for production — because the executor owns the kill.

The deadline is monotonic, absolute, and taken before enqueue: `deadline = monotonic() + 1.0`. Every stage — queue admission, worker acquisition, IPC, compile, match, response IPC — receives the remaining budget; no stage resets the clock. A pattern the queue cannot admit before its deadline fails without running; an admitted pattern runs with what remains. Admission is bounded: a bounded queue alone still permits 900 ms of queue wait plus a 1000 ms worker timeout — a 1900 ms request that breaks the 1000 ms deadline callers are promised.

This supersedes ADR-0004's sentence "Queueing changes latency only. It must not change tool output": under a saturated pool, a pattern the reference would have executed and matched can fail here as a timeout. Recorded fixtures are single-call and unaffected — the difference appears only under concurrent saturation, which no fixture exercises — and P9's load suite owns that behavior and measures the saturation threshold. This is the resolution of the design review's F3 finding; ADR-0004's queueing sentences read as amended by this ADR.

The fuzz corpus hammers anchors, empty matches, character classes, escaped classes, Unicode, case folding, alternation, quantifiers, unsupported flags, rewrites, invalid patterns, candidate boundary behavior, and timeouts, with Node 24 as the oracle where possible.

## Considered Options

- **`translate(pattern) -> compiled`** — rejected: returning a compiled callable from the pure module re-couples it to `regex` execution semantics and invites in-process execution in production.
- **Deadline anchored at slot start** — rejected: a 1000 ms execution timeout is not a 1000 ms request deadline if a job can sit in a queue first; the caller gets the budget the tool promises, even under load.

## Consequences

- The dialect module is pure and fuzz-testable without worker processes.
- The pool adapter owns: bounded queue, admission against the deadline, worker execution, timeout, kill and replacement, result transport, and per-call cancellation (ADR-0011).
- Under saturation, extract output can diverge from the reference (timeouts the reference would not produce); load tests assert the bounded behavior, not reference equality.
- `jev_extract`'s hard invariant stands: `value ∈ candidates(document) ∪ {null}`.

## Amendment (2026-09-22): the second adapter is made real

- `extract/candidates.py` owns `find_candidates(executor, pattern, flags, units) -> Found | Refused`.
  It normalizes flags, translates to `dialect.Translated`, takes the absolute deadline before
  admission, and calls `RegexExecutor.find`. It converts matches from UTF-16 unit space and maps
  refusals to their reasons and outcome labels. Cancellation propagates (ADR-0011).
- `RegexExecutor` defines `find` and `aclose`. `Runtime` defaults to `ProcessRegexExecutor` and
  closes the executor it owns, including one injected through its constructor.
  `InProcessRegexExecutor` runs trusted test inputs on the calling thread.
- The differential suite calls `find_candidates` with both adapters. The process run also covers
  IPC and the candidate caps passed in each job from `limits.EXTRACT`.

## Amendment (2026-11-19): compile inside the execution budget

On the server event loop, the dialect parses and rewrites caller patterns. Both executors compile
and match through `match_all`. The process executor performs both in the worker under the original
deadline, and the IPC job carries the remaining budget in seconds for the worker's fallback timer.
Compiler diagnostics travel back as ordinary invalid results so `find_candidates` preserves the
pattern rejection reason and the worker can serve another job.
