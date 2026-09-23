---
status: accepted
---
# Same-origin redirects only on the HTTP providers

The reference POSTs judgment state — evidence, diffs, claims — with `fetch`, which follows
redirects; on a cross-origin `307`/`308` the request body is re-sent to the redirect target. The
state body is more sensitive than the API key (which httpx already strips on cross-origin
redirects, verified in `httpx._client._redirect_headers`): a redirect can move the caller's state
across a trust boundary with no credential involved. Python enforces the policy the reference
never stated: **the redirect target's origin must equal the initial request's origin** — scheme,
host, and effective port. A redirect that changes origin is rejected *before the next request
leaves the process*, with an owned `{label} request failed:` error text (redacted like every
provider error, ADR-0008) and a stderr warning that names the blocked origin, never the state.

Implementation rules (ADR-0023):

- Redirect *requests are built by httpx*, never by us: `follow_redirects=True` stays, and a client
  request hook rejects a cross-origin hop before it is sent. No second HTTP implementation, no
  hand-rolled 301/302/303/307/308 method rewriting — every status httpx supports keeps exactly
  httpx's semantics, and httpx's own redirect cap is the hop limit.
- The allowed origin is the provider's configured request URL (process config, ADR-0008), fixed
  per provider instance — there is no per-call state to race.
- Relative `Location` values are resolved by httpx against the current URL, then judged by the
  same origin rule.
- The `typesafe-sdk` transport rides the SDK's own `httpx2` client and is outside this guard's
  reach; its redirect policy is a registered gap (`typesafe-sdk-redirect-policy`), not a claim.

## Consequences

- A provider (or anything that can influence its responses) can no longer exfiltrate judgment
  state by redirecting; same-origin redirects still work for ordinary endpoint moves.
- Cross-origin redirect behavior is a Sanctioned Divergence from fetch's default, registered as
  `same-origin-redirects-only`; the contract suite pins blocked cross-origin, followed
  same-origin (301/302/303/307/308), relative `Location`, and the redirect-cap error.
