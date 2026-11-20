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
the recovery path for the call. Every attempt is bounded and eligible failures are retried, all
inside the caller's deadline when there is one (the hook's 30 s, ADR-0035; setup's
30 s). This supersedes ADR-0021's "Tier A keeps the reference's no-deadline behavior"; the registry
entry `stdio-no-provider-deadline` becomes `stdio-attempt-deadline`.

### Retry eligibility

HTTP `429` and transport failures while establishing a connection or acquiring a pool slot are
eligible for retry. HTTP `408`, `5xx`, read and write failures, resets with unknown delivery,
and unspecified timeouts are terminal. Local protocol errors, unsupported schemes, validation and
configuration errors, credentials in a URL, and cross-origin redirect refusals (ADR-0023) are also
terminal. Cancellation propagates immediately during an attempt or backoff sleep.

### The numbers, and the evidence behind them

- **3 attempts** (one call + two retries): the SDK's own default `max_retries=2`.
- **Backoff 0.5 s doubling to a 5.0 s cap, 25% subtractive jitter**: the SDK's default shape.
- **`Retry-After` (delay seconds or HTTP date) and `retry-after-ms` set a minimum**: use the
  larger of the server hint and generated backoff. The overall budget bounds admission.
- **Per-attempt timeout 30 s.** Measured provider latency in this repo's records is 70–500 ms
  (`docs/ROADMAP.md`); the official pages say most queries complete in about 100 ms
  (https://docs.typesafe.ai/concepts/how-to-build-with-system-one.md).
  The measured bench150 round trips — through the whole MCP server, so an upper bound on provider
  latency — ran median 464.6 ms, p90 1245.3 ms, p95 1468.8 ms over n=157 calls
  (`evals/reports/bench150.md`). 30 s is ~20× the measured p95, and it matches the whole-call
  budgets this repo already considered sane for one provider call (the hook's and setup's 30 s).
  The largest legitimate calls are still one model call each: a `jev_gate` at its 200K-character
  limit or a 250-candidate `jev_find` scale the input size, not the round trips, and stay inside
  the deadline with headroom.
- **Overall budget 90 s**: three attempts at their 30 s deadline plus bounded delays. It is a hard
  bound on the whole call: every attempt is capped at the budget time left, not only at the
  per-attempt timeout, so with `timeout=None` the worst case — three hung attempts plus delays —
  still finishes at or under 90 s. A retry requires at least 1 s after elapsed time and the
  delay are deducted. When the caller passed a deadline, the caller's remaining budget binds
  instead — retries can never run past it. A timeout the caller's own remaining time cut keeps the
  caller-timeout text; a timeout the policy budget cut (only possible with a deadline above the
  budget) is exhausted-retry text, naming the attempts.

### Double billing

An attempt can be billed if a read error, reset, or unspecified timeout follows acceptance.
Automatic retries stop on those failures. A connection or pool-acquisition failure precedes
delivery, and HTTP `429` explicitly rejects the request. Callers with hard caps can opt out
entirely. Generated backoff stays strictly positive to avoid amplifying the failure.

### One injection point turns retries off

`retry=RetryPolicy(max_attempts=1)` (`NO_RETRIES`) on any provider constructor, or
`resolve_provider(settings, retry=NO_RETRIES)`. A `max_attempts=1` policy still bounds its single
attempt. The paid runners (`evals/runners/live.py`, `tests/security/test_live_typesafe.py`) keep
exact call caps with it, and the replay-based parity and contract fixtures stay single-attempt,
deterministic, and byte-identical to their recordings — a fixture's 408/429/5xx exchange never
retries.

### Errors and logs

A single-attempt policy keeps the recorded error wording. Under a retry-enabled policy, an
ambiguous timeout or transport failure can add “Completion is uncertain.” When retries are
exhausted, the final error names the provider, the attempt count, and the last failure:

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

## Amendment (2026-09-27): the reference timeout claim, and the latency cite

`stdio-attempt-deadline`'s reference sentence that no provider request carries a timeout anywhere
in askJev is withdrawn. askJev passes no timeout of its own, but the typesafe path inherits the
pinned SDK 0.6.0 default of 10s per attempt. The fetch-based providers still pass no timeout.
The 30 s choice above does not depend on calling 70–500 ms a published official figure; that range
is this repo's recorded calls, and the official pages say about 100 ms.

## Amendment (2026-11-18) — uncertain delivery is terminal

The default retries only an explicitly rejected HTTP 429 or a transport failure establishing a
connection or acquiring a connection-pool slot. A read, write, reset, unspecified timeout or 5xx
can follow acceptance of a billable evaluation. Without a provider idempotency contract, replay
could charge twice. A server retry delay is a minimum. If that minimum reaches the remaining
deadline, the attempt ends and the server's response is returned. The attempt cap and generated
backoff retain their existing bounds.

Providers request identity encoding, cap successful responses at 8 MiB, and truncate streamed
error bodies at 64 KiB. An encoding the client did not request or an oversized successful
response ends the call before JSON decoding.

Paid callers inspecting a provider failure can read `ProviderError.delivery_uncertain` under every
retry policy, including `NO_RETRIES`. A true value means the provider may have accepted the
evaluation. The single-attempt message retains its historical wording.

## Amendment (2026-11-19) — transport deadlines and bounded response handling

- Raw HTTP and the TypeSafe SDK set connect and pool timeouts to one quarter of the active
  attempt cap. Connection acquisition and establishment can time out before the outer deadline,
  preserving pre-delivery retry classification.
- Retry delay is the greater of generated backoff and the server minimum. The next attempt
  requires at least 1 s after that delay within the caller or policy horizon, allowing headroom
  over Jev's measured 70–500 ms latency. If a 429 retry cannot fit, return that response's quota
  error.
- The shared response reader retains up to 64 KiB from streamed error bodies. Already-buffered
  errors pass through with their original content. Both preserve the HTTP status, provider label,
  and retry classification, and the diagnostic uses a short excerpt. A successful body over
  8 MiB still raises a size-limit error. Error serialization handles the full nesting depth
  of the JSON decoder.
- Delete `ProviderError.delivery_uncertain`. F33 requested the field for paid callers, which
  never read it. The eval ledger books conservative bounds. The retry-enabled path still uses
  “Completion is uncertain.” for ambiguous timeouts and transport failures.
- The shared envelope parser accepts upstream model and request-ID strings of 1–1,024 printable
  ASCII characters. Invalid strings are treated as absent. A valid body request ID takes
  precedence over a valid response header. Cache reads apply the same bounds to existing
  entries. An invalid recorded model or request ID makes the entry a cache miss. Adapters share
  case-insensitive header lookup and default port values.
- Delete the unused `RetryPolicy.statuses` field. HTTP 429 remains the sole retryable status.

## Amendment (2026-11-19): The shared HTTP client handles TypeSafe

ADR-0084 removes the TypeSafe SDK and its retry suppression. All providers use the same
HTTP transport and `JevProvider.evaluate` owns retries.
