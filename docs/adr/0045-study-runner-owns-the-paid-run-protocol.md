---
status: accepted
---

# One study runner owns the paid run protocol

ADR-0026 put the agent run, the relay, and the spend ledger in one place each, and left grading and record schemas in the A/B study and the bench. Both studies still owned the same remaining protocol: run the agent, build a record, write `result.json`, and on failure write a second record and book. Those booking handlers had already drifted once. A runner copied from either handler would have copied that bug, so the money rule landed first as `book_outcome` (ADR-0026's ledger, extended in `evals/spend.py`).

`evals/study_runner.py` now owns the protocol for both paid harnesses. Row contents stay in `evals/ab/run.py` and `evals/bench/run.py`.

- `write_record` writes the success record. On `Exception` it writes the failed record and re-raises. It does not catch `KeyboardInterrupt`, `SystemExit`, or `CancelledError`.
- `record_row` builds a record from one key sequence. Each package has one sequence, the success record's keys, and fills it for both outcomes. A missing or extra key raises. The failed record does not keep its own key list.
- `run_recorded` books the selected cost once. A/B selects `cost_usd`. The bench selects `jev_cost_usd` because its cap covers only Jev spend. A finite, nonnegative price books its recorded value, including an explicit zero. A missing or invalid price reserves the full per-run bound.
- Success uses the returned record. On `KeyboardInterrupt`, `SystemExit`, or a named `file_cost` exception, the runner reads the selected cost from the saved result and re-raises. The bench uses `file_cost` for `HarnessMismatchError` and `WrongModelError`. A rejected key or unreachable provider uses the same booking rule and returns a stop reason. Other ordinary exceptions book the full bound. Cancellation propagates unbooked.
- Before scheduling new paid work, each study reconciles previously started attempts with the ledger. The bench validates the saved results and reserves at least the bound for an unusable row. If a historical booking is too small to cover the required charge, the bench stops for reconciliation.

## Consequences

- An interruption between result publication and ledger booking preserves the same charge on recovery. Bench grading failures record unknown spend as `null`. On recovery, older grading failures with a placeholder zero reserve the run bound.
- No parity surface. `evals/relay.py` and `evals/agent.py` are unchanged.
- This amends ADR-0026: the protocol around the shared agent run and ledger has one owner. Grading, gates, and the fields inside a row stay in each caller.

## Amendment 2026-11-19: account for a launch before capture finishes

- Both agent launchers durably publish `agent.started` before capture, syncing the marker and
  its directory entries. A crash after that handoff leaves a chargeable attempt even if the
  transcript never appears. Recovery still recognizes transcripts and results from older runs.
- Both studies use `study_runner` to recover a started attempt once and exclude it from scheduling.
  A validated result supplies the selected finite charge, including a measured zero. A missing
  charge reserves the bound. An invalid result reserves at least the bound and can only raise
  that amount with a finite charge whose run ID matches the attempt. Existing bookings below
  the charge supported by a valid result stop the study for reconciliation.
- An infrastructure stop carries a measured charge when one is available. Otherwise the runner
  uses the saved result or the bound after launch, and leaves failures before the launch handoff
  unbooked. It stops without synthesizing an agent failure. A/B failure records use `null` for
  unmeasured Jev spend.
- A/B reads pinned metadata for resume and reporting through the bounded artifact reader.

## Amendment 2026-11-19: book confined A/B Jev usage from broker receipts

A/B prices TypeSafe input tokens from the private broker journal in ADR-0074. Every started
request needs a settlement with upstream usage, and the journal needs a matching final seal.
Missing, unreadable, inconsistent, or incomplete receipts book at least the run bound for Jev.
Any known receipt costs above the bound are retained.

The relay remains diagnostic evidence. The study compares its billable input-token total
against the broker's total and records tampering or disagreement in `jev_metering_issue`.
Metering issues exclude the pair from the outcome analysis. A valid receipt set still prices
the run when the relay fails this check. Confined A/B post-processing failures preserve the
receipt-backed charge in a failed result for the shared study runner to book.
