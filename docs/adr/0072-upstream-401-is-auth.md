---
status: accepted
---
# An upstream 401 is auth

A missing local credential was already `auth`. An upstream 401 — the API's "missing or invalid API key" — was `provider`, so a rejected key looked like an outage. The owner decided to map that status to `auth` and leave the other 4xx codes alone.

## Decision

- `error_code` reads the status token in `{label} {status}: {body}` (`providers/base.py` `_status_error`). A token of `401` is `auth`.
- 403, 404, and 422 stay `provider`. A `401` that appears only in the body of another status stays `provider`.
- The error text is unchanged. Only the code changes (ADR-0062).
- This is divergence `upstream-401-is-auth`.

## Consequences

- A client that branches on `auth` can tell a rejected key from an outage.
- A client that treated every provider failure as an outage now sees `auth` for a 401.
