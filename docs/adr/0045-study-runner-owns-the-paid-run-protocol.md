---
status: accepted
---

# One study runner owns the paid run protocol

ADR-0026 put the agent run, the relay, and the spend ledger in one place each, and left grading and record schemas in the A/B study and the bench. Both studies still owned the same remaining protocol: run the agent, build a record, write `result.json`, and on failure write a second record and book. Those booking handlers had already drifted once. A runner copied from either handler would have copied that bug, so the money rule landed first as `book_outcome` (ADR-0026's ledger, extended in `evals/spend.py`).

`evals/study_runner.py` now owns the protocol for both paid harnesses. Row contents stay in `evals/ab/run.py` and `evals/bench/run.py`.

- `write_record` writes the success record. On `Exception` it writes the failed record and re-raises. It does not catch `KeyboardInterrupt`, `SystemExit`, or `CancelledError`.
- `record_row` builds a record from one key sequence. Each package has one sequence, the success record's keys, and fills it for both outcomes. A missing or extra key raises. The failed record does not keep its own key list.
- `run_recorded` books once. `KeyboardInterrupt` and `SystemExit` call `book_outcome`, which reads `cost_usd`. Success books `record[cost_key]`. The A/B study uses `cost_usd`. The bench uses `jev_cost_usd`, because its dollar cap does not include agent spend. `Exception` types passed as `file_cost` book that field from the written file and re-raise; the bench passes `HarnessMismatchError` and `WrongModelError`. `stop` returns a reason after the same booking instead of re-raising; the bench uses it for `AuthRejected` and `ServerUnreachable`, with `file_default=0` when the field is missing. Any other `Exception` calls `record_unfinished` (the run bound) and does not read the file. `CancelledError` is not caught and is not booked.
- Resume still calls `book_outcome` before `can_start`. That call is the money rule, not a second handler.

## Consequences

- Ledger amounts and result records are unchanged. A post-processing failure is still charged the run bound. A harness mismatch, wrong model, rejected key, or unreachable provider is still charged the bench's written `jev_cost_usd`, or 0 when that field is absent. The A/B study still charges `cost_usd`.
- No parity surface. `evals/relay.py` and `evals/agent.py` are unchanged.
- This amends ADR-0026: the protocol around the shared agent run and ledger has one owner. Grading, gates, and the fields inside a row stay in each caller.
