---
status: accepted
---
# The server starts its regex pool warm

On a review run, the security stage failed 3-of-3 in a Linux container limited to one CPU
(`docker run --cpus 1`, non-root) at `test_corpus_times_out_inside_the_deadline_while_other_calls_are_served`,
while GitHub's 4-vCPU runners and unlimited-CPU containers passed. A benign `jev_extract` came back
`invalid_pattern` — "regex timed out after 1000ms" — in the middle of ten concurrent catastrophic
patterns, every one of which finished at its own ~1.0 s deadline. The storm test's invariant
(ADR-0004, ADR-0018) is that pathological patterns never stall the server: benign calls are served
throughout the storm, not after it. A violation is a release blocker.

## Diagnosis

The pool held one warm worker and started every other worker synchronously inside the caller's
deadline (`_find` → `_start`). The storm's arrival burst is ten pathological calls plus the benign
loop at the same event-loop tick, so eleven `python -m jev_judge_mcp.extract.worker` starts raced on
one CPU of cgroup quota (`cpu.max` = `100000 100000`). Under that saturation a worker start is not
the ~40 ms it costs unloaded; the benign call's start was scheduled out past its whole 1 s budget
and returned the timeout refusal. Filling the idle pool before the storm (same process count, same
load) was the counterfactual: the benign call then returned in ≤ 93 ms throughout the storm, every
pathological call still died at its own deadline, and with only one warm worker the benign call
still hit 0.993 s — the burst's synchronous starts, not the process count, are the failure. The
production pool made this worse than the pre-fix test: the server built its executor
(`tools/base.py`) and never warmed it at all, so even the first call paid a start inside its
deadline and a cold storm paid all of them. The storm test's own `await executor.warm()` was the
only warm pool in the tree — the repair never reached production until `serve` did the warming.

## Decision

**The server's startup path fills the pool before any transport runs, and the default size stays
core-based.**

- `serve` awaits `Toolset.awarm` → `Runtime.awarm` → `ProcessRegexExecutor.warm` before the stdio or
  HTTP loop starts. Warming overlaps no demand, so live workers never exceed the pool's size. The
  warm is best effort: a pool that cannot start workers at startup logs a warning and tries again on
  demand, so the server still comes up for the tools that need no pool. The storm test warms through
  that same production path with an explicit size — not by calling the pool by hand.
- The default size stays `min(8, os.cpu_count())`. Quota-aware sizing (cap the default at the
  cgroup's `cpu.max`) was tried and rejected with evidence: at a one-CPU quota it defaults the pool
  to one slot, and one pathological pattern then holds the only slot for its whole deadline while
  benign extracts wait on the semaphore and time out — the head-of-line stall ADR-0004/0018 forbid.
  The storm contract's premise is spare slots, not CPU honesty: oversubscribed spins are survivable
  (each is killed at its own deadline; measured benign round-trips stay ≤ 93 ms of the shared
  budget), while slot starvation is not. Explicit `size` arguments stay explicit: the caller owns
  that capacity contract, and the storm test's `size=len(CATASTROPHIC) + 2` is the headroom its
  invariant needs. A storm larger than the pool still times queued calls out at their own deadlines
  — that is the refusal working, not a stall.
- The storm contract now runs on every CI push under a one-CPU quota, non-root, in the uv bookworm
  container pinned by digest (the `security-one-cpu` job): spare runner cores are exactly what
  masked this failure, so the guard spends them on nothing.

This changes no wire behavior, no reason text, and no deadline rule (ADR-0016); a slot killed by a
deadline or a cancel is still replaced on the next demand.

## Consequences

Every server start pays `size` process starts up front (~0.3 s at the default eight on one quota'd
CPU) and holds `size` resident small interpreters instead of one — the pool was already willing to
hold that many under load. The one-CPU storm is now rerun through the production startup path on
every push, red on the pre-fix code (the production-shaped storm without the warm failed 2-of-3),
green after.
