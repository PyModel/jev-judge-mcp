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
