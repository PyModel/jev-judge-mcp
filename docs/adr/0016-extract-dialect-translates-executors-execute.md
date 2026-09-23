---
status: accepted
---
# extract: the dialect translates; executors execute and own time

`extract/dialect.py` validates pattern syntax and flags, rewrites the dialect, and fixes anchor and case-fold semantics (ADR-0004); it returns a frozen `TranslatedPattern` and never compiles or matches. Compilation and matching belong to the execution adapters — an in-process adapter for the differential fuzz suite and the process-pool adapter for production — because executing a pattern against input is the dangerous operation, and the kill must live where the execution lives. Two adapters make the seam real.

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

The type this ADR calls `TranslatedPattern` is named `Translated` in `extract/dialect.py`. Read
this ADR with that name.

The review found that only one adapter exists, so the seam this ADR names is hypothetical.
`tools/extract.py` `_match` assembles normalize-flags → translate → `RegexPool.run` → from-units and
the four-way exception mapping. `tests/parity/test_extract_differential.py` re-assembles its own
copy with the synchronous `find_candidates`, so the Node oracle never checks the sequence
production runs. `Runtime` hardwires `RegexPool()`, and four tests replace it by reassigning an
attribute.

Decision (implementation pending):
- `extract/candidates.py` owns the sequence behind one function,
  `find(executor, pattern, flags, units) -> FieldCandidates`. It maps rejected, saturated (checked
  before timeout, since `RegexPoolSaturated` subclasses `RegexTimeout`), timeout and worker errors
  to reasons and outcome labels. It never swallows cancellation (ADR-0011).
- The seam is a `CandidateExecutor` protocol (`run`, `aclose`) with two adapters. `RegexPool` is the
  production adapter. `InProcessExecutor` serves the differential suite and unit tests only. It
  blocks the event loop and is never the default, and a guard test pins that.
- The regex timeout and pool-saturation reason texts move to `candidates.py`. `worker.py` keeps the
  exceptions and owns time; the protocol takes no deadline.
- `Runtime` receives the executor through its constructor, as it receives `provider_factory`. An
  injected executor is owned and closed by `Runtime.aclose`.
- The worker reads its caps from `limits.EXTRACT` through the job instead of keeping its own copies.
- The differential corpus also replays once through `RegexPool` in the parity stage, covering the
  pickle transport and the worker's caps.
