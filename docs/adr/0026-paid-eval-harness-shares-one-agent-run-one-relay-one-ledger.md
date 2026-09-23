---
status: accepted
---
# Paid eval harnesses share one agent run, one relay, and one spend ledger

The P8 A/B pilot (`evals/ab/`) and the 150-question bench (`evals/bench/`) each implement the same
three mechanisms, and their copies have drifted.

**Agent run.** Both `run_one` functions create two temp dirs, write an O_EXCL 0600 `mcp.json`, run
`claude` under a timeout, decode the stream, apply the same status rule, and remove the temp dirs
and scrub the secret. They drifted in several ways:
- A timed-out run keeps `rc = 0` in the pilot and records `None` in the bench.
- Both ignore the injected `environ` and read `os.environ` instead.
- The bench fakes the pilot's arm A config, then overwrites it.
- The bench imports `secret_scrub` from the pilot.
- The pilot's copy has no offline test.

**Recording relay.** `ab/proxy.py` and `bench/proxy.py` duplicate the stdio relay: the pending map,
the round-trip timing, the log files, and Popen. Only the pilot proxy closes the client's stdout, and
the bench proxy drops the message of a JSON-RPC error. The bench borrows eval-live's
`LIVE_REQUEST_CAP` (cases × repeats per L3 run) as its per-agent-run Jev call cap, so changing one
silently moves the other.

**Spend accounting.** Run cost is computed four times:
- `pilot.run_cost` and `bench.run_cost`, which is a verbatim copy;
- two inline copies in `ab/report.py`.

The Jev price lives in the markdown renderer. `BenchLedger` overrides the whole budget check to
change two caps, and `COST_STOP_USD = 22.75` is typed in by hand.

The review also found bugs in these copies:
- **B1 (spend).** When post-processing raises (the pilot's `grade`, or the bench's span parse), the
  run is never written to the ledger. A resume then runs it, and pays for it, again.
- **B2 (spend).** A timed-out run has no result event, so `agent_cost_usd` is `None`, and `or 0`
  records **$0** for a run that may have spent its whole `--max-budget-usd`.
- **B3 (study).** A proxied call that never gets a response (the server exits, or never answers)
  leaves no log row. The bench cross-check then raises `HarnessMismatchError`, and the paid study
  stops as if the harness were broken.
- **B4 (latent).** The relay pairs a response to a pending call by id without first checking that
  the message has no `method`. A server-to-client request whose id collides with a pending call
  would be logged as that call's response.
- **B5.** The pilot's `run_one` does not guard against an empty secret. `secret_scrub("")` would
  insert the redaction marker between every byte.

## Decision

- **`evals/agent_run.py`** owns one sandboxed agent run:
  - `AgentRun(argv, mcp_config, secret, env, timeout_s, jev_expected)`, with a non-empty secret enforced;
  - a `run_agent(spec, run_dir, prepare)` context manager that yields `Outcome(status, rc, trace, wall_s, workdir)`.
  - The workdir stays alive for the caller's grading, then is removed and scrubbed on exit.
  - A timeout records `rc = None`.
  - The seam is `argv[0]`: the resolved `claude` binary live, and `tests/support/bench_agent.py` offline.
  - The caller passes `env`, built by `agent_env(environ)`.
  - `secret_scrub` lives here.
  - `arms` composes `mcp_config` from `harness_server()` and `jev_server(...)`, so the bench stops faking arm A.
- **Grading, gates and record schemas stay in each caller.** A stop is returned as a value, not raised
  from `finally`.
- **Post-processing failures are still recorded (fixes B1).** The run is written as a failed record
  with a ledger entry, so the "never retried" rule holds.
- **`evals/relay.py`** owns the relay:
  - `serve(argv, prog, recorder_factory)`, with argv, Popen, stderr, and a single locked stdout
    writer;
  - it ignores messages that carry `method` when pairing responses (fixes B4);
  - at child EOF it calls `Recorder.close()`, which logs each pending call as
    `unanswered: true, is_error: true` (fixes B3). The cross-check sees an errored call instead of a
    harness mismatch.
- **Two recorder adapters stay separate.** The P8 recorder logs no arguments or text, and keeps that
  guarantee in its own code rather than in a field list. The bench recorder logs both and refuses
  calls past its own `JEV_CALL_CAP`. That cap is justified by `JEV_RUN_BOUND_USD` and no longer
  imported from `runners/live`.
- **The spend ledger** (`evals/ab/ledger.py`) owns spend:
  - it owns the price (`JEV_USD_PER_MTOK_INPUT`, `jev_cost_usd`), `jev_tokens` and `run_cost`;
  - `Caps(runs, usd, run_bound, batch)` has two instances, `PILOT_CAPS` and `BENCH_CAPS`;
  - `Ledger.record(run_id, record)` computes the cost itself;
  - the cost-stop budget is derived as `usd - run_bound`;
  - `BenchLedger` is deleted;
  - the file format stays `{"runs": {id: float}}`, and the blocker texts are kept.
- **A run with no reported agent cost is charged `caps.run_bound`** (the worst case) and flagged,
  never $0 (fixes B2).
- **Each study keeps its own cap.** Budget pooling is out of scope.

## Considered Options

- **One `RecordPolicy` config instead of two recorders.** Rejected: nothing reappears if it is
  deleted, and it would turn P8's no-arguments guarantee into a field list that one edit could
  break.
- **Keep the duplication.** Rejected: B1, B3 and B4 would each need fixing twice, and the copies had
  already drifted.
- **Freeze the pilot code as the record of the P8 run.** Rejected: it was already edited after the run
  (`9b5e163`, `12a4607`). The committed `evals/reports/p8-pilot.md` is re-rendered only from
  records, and no run code touches it.

## Consequences

- The committed P8 report and every committed number are unchanged. Ledgers and raw records are
  gitignored.
- The pilot's run path gains offline coverage through the stub agent.
- `rc = None` on timeout is cosmetic for P8: no record stores `rc`, and all nine P8 runs were `ok`.
- The raw `evals/reports/p8-pilot/` directory, with its ledger, is not kept, so a fresh `make ab`
  would spend again and overwrite the committed report. The pilot refuses to write `p8-pilot.md`
  when records exist neither locally nor in a ledger.

## As built (branch `fm/jev-eval-harness-deepen`)

The approving review renamed parts of this decision; the code follows the approval:
- `evals/agent_run.py` → `evals/agent.py`; `AgentRun` / `Outcome` → `AgentCommand` (argv given the config
  path, timeout, env) / `AgentRunResult`. The agent env is `base_env` (the caller's `agent_env(environ)`)
  updated with `AgentCommand.env`.
- `Caps` / `Ledger` in `evals/ab/ledger.py` → `SpendPolicy` (frozen: caps, run bound, optional batch
  bound, the Jev price) and `SpendLedger` (`cost_of`, `can_start`, `record`) in `evals/spend.py`; the
  approved name for the pair is SpendBudget. Each record stores its cost, and the ledger books that
  stored total, instead of `Ledger.record(run_id, record)` pricing the record.
- The bench's call cap is `BENCH_REQUEST_CAP` in `evals/bench/proxy.py`, not `JEV_CALL_CAP`.
- B1 books a run that raised at the run bound (`SpendLedger.record_unfinished`) and re-raises; it
  writes no failed record. B2 and B5 are fixed as decided; `COST_STOP_USD` is derived.

Not done on that branch: the `harness_server()` / `jev_server()` composition in `arms`, a stop returned
as a value, and T5's guard against overwriting `p8-pilot.md`.

The relay merge landed on branch `fm/jev-relay-one`: `evals/relay.py` `serve()` with the `Recorder`
protocol, and the two recorders stay in `evals/ab/proxy.py` and `evals/bench/proxy.py`. B3 and B4 are
fixed as decided; an `unanswered` row matches a stream error or a missing stream result in the bench
cross-check. The bench recorder also refuses a `tools/call` without an id and a batch array that holds
one, because neither can be paired or counted against `BENCH_REQUEST_CAP`.

ADR-0045 names `evals.study_runner` as the owner of run → record → write → failed record → book.
Grading, gates, and the fields inside a row stay in each caller, as decided above.
