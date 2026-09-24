---
status: accepted
---
# One bounded retry policy owns upstream Jev retries

Until now a provider attempt had no deadline unless the caller set one (`Runtime.ask` passed
`timeout=None`, ADR-0021), and only the `typesafe` provider retried at all — implicitly, under the
SDK's default `RetryPolicy`. `openrouter`, `cloudflare` and `compatible` sent exactly once with
`timeout=None`: one hung connection held a stdio tool call until the client cancelled it, and one
transient 503 failed a call that one more attempt would have passed.

## Decision

**Jev owns retries for all four providers, under one explicit policy** (`providers/retry.py`). The
typesafe-sdk transport is constructed with `RetryPolicy(max_retries=0)` so the SDK's attempts can
never multiply jev's; the raw providers run the same loop with no second implementation. The
alternative — letting the SDK stay the retry owner for `typesafe` and mirroring its policy by hand
in `HttpProvider` — keeps two retry engines and two test suites over one definition. One owner
leaves one definition and one test suite, and the SDK's policy is explicitly disabled rather than
implicitly default.

`Runtime.ask` still passes no whole-call deadline: the client's MCP cancellation (ADR-0011) remains
the recovery path for the call. What changes is that every attempt is bounded and transient failures
are retried, all inside the caller's deadline when there is one (the hook's 30 s, ADR-0035; setup's
30 s). This supersedes ADR-0021's "Tier A keeps the reference's no-deadline behavior"; the registry
entry `stdio-no-provider-deadline` becomes `stdio-attempt-deadline`.

### Retried, and never retried

Retried: a connection failure before any response (refused, reset, DNS), a per-attempt timeout, and
the status codes `408`, `429`, and `500`–`599` — the SDK's own default set. Never retried: any other
4xx, an envelope or validation error, `ProviderConfigError`, the credentials-in-URL refusal, the
cross-origin redirect refusal (ADR-0023), and a cancelled call. Cancellation is a `BaseException`:
it is never classified, never swallowed, and stops an attempt or a backoff sleep at once.

### The numbers, and the evidence behind them

- **3 attempts** (one call + two retries): the SDK's own default `max_retries=2`.
- **Backoff 0.5 s doubling to a 5.0 s cap, 25% subtractive jitter**: the SDK's default shape.
- **`Retry-After` (delay seconds or HTTP date) and `retry-after-ms` honored, capped at 5 s**: the
  SDK honors the headers but not a cap; an unbounded server-chosen wait is not a bounded policy.
- **Per-attempt timeout 30 s.** Jev's published provider latency is 70–500 ms (`docs/ROADMAP.md`).
  The measured bench150 round trips — through the whole MCP server, so an upper bound on provider
  latency — ran median 464.6 ms, p90 1245.3 ms, p95 1468.8 ms over n=157 calls
  (`evals/reports/bench150.md`). 30 s is ~20× the measured p95, and it matches the whole-call
  budgets this repo already considered sane for one provider call (the hook's and setup's 30 s).
  The largest legitimate calls are still one model call each: a `jev_gate` at its 200K-character
  limit or a 250-candidate `jev_find` scale the input size, not the round trips, and stay inside
  the deadline with headroom.
- **Overall budget 90 s**: three attempts at their 30 s deadline plus bounded delays. A retry whose
  delay would reach the budget (elapsed + delay ≥ budget) is skipped. When the caller passed a
  deadline, the caller's remaining budget binds instead — retries can never run past it.

### Double billing

Retrying an attempt that timed out after the server did the work can bill the call twice. A Jev
evaluation is a read-only judgment, so the risk is cost, not state corruption; the attempt cap
bounds it at three bills per call. Retries on connection refusals, resets, 408/429 and 5xx carry no
such ambiguity — the request never completed — and a caller with a hard cap (below) opts out
entirely.

### One injection point turns retries off

`retry=RetryPolicy(max_attempts=1)` (`NO_RETRIES`) on any provider constructor, or
`resolve_provider(settings, retry=NO_RETRIES)`. A `max_attempts=1` policy still bounds its single
attempt. The paid runners (`evals/runners/live.py`, `tests/security/test_live_typesafe.py`) keep
exact call caps with it, and the replay-based parity and contract fixtures stay single-attempt,
deterministic, and byte-identical to their recordings — a fixture's 408/429/5xx exchange never
retries.

### Errors and logs

A failure with no retry behind it — the first attempt failed and the policy is off, the failure is
not transient, or the caller's deadline ended the call — keeps today's exact error text, byte for
byte. When retries happened and all failed, the final error names the provider, the attempt count,
and the last failure:

`{label} request failed after {n} attempts: last failure: {status: body | the attempt timed out | cause}`

That final text is new MCP-visible behavior over stdio; it is sanctioned by this ADR, registered in
`docs/reference/divergences.json` (`stdio-attempt-deadline`), and pinned in
`tests/contract/test_provider_retries.py`. Redaction (ADR-0008) and the 200-unit error-body cut are
unchanged: the quoted last failure is the already-redacted, already-cut text, redacted once more on
the way out. Each retry logs one warning line with allowlisted fields only — provider label,
attempt n of N, status code or error class, delay — never a URL, header, body, state, key, or
token.

No new environment settings: the policy is one constant for every operator, and nothing observed
asked for a knob. Retry tests inject the sleep, the clock, and the jitter source; they stay offline
and deterministic.

## Consequences

- A hung provider now costs a stdio call at most three bounded attempts and ~90 s, not an unbounded
  wait; MCP cancellation still stops everything at once.
- The unit test that pinned "TypeSafe keeps the SDK's default retries" now pins the opposite: the
  SDK never retries, and jev's three attempts are exactly three requests.
- `Runtime`'s docstring claim ("no deadline") narrows to "no whole-call deadline".
- Replay fixtures, contract fixtures, and paid runs must pass `NO_RETRIES`; the injection point is
  the provider constructor's `retry` parameter, and nothing else.
