---
status: accepted
---

# Installer entries request a qualifying Python, and an unreleased tree says so

Amends ADR-0051. The end-to-end dogfood of the 0.2.0 release found three installer defects in
one class: entries that could not start on ordinary machines, a post-write check that hid why,
and a default pin that silently exercised the wrong build. `--from-checkout` and the version
pin itself (ADR-0051) are unchanged.

## Decision

- **Python request.** Every entry the installer writes — for every agent, for the
  version-pinned PyPI launch and for `--from-checkout` — carries
  `--python <Requires-Python>` as an argv element, taken from the installed distribution's
  own metadata, never hardcoded. `uvx` otherwise resolves the package against the first
  interpreter it discovers; on a machine whose first Python is below 3.12 (Ubuntu 22.04's
  3.10, macOS system 3.9) the entry fails with "the current Python version does not satisfy
  Python>=3.12". With the request, uv picks a qualifying interpreter, downloading a managed
  one if none is installed. Entries stay argument arrays; the request rides as one element.
  If the metadata declares no `Requires-Python`, the entry simply omits the flag.
- **Upgrade.** Re-running `install` rewrites an entry this installer owns that lacks the
  request; the state hash covers the whole written entry, so the old shape differs from the
  desired one and lands on the ordinary update path. No migration command.
- **Verify observability.** When the post-write handshake fails, the `VerifyError` carries
  the last lines of the child's stderr — the actionable resolver message — through the
  installer's existing redaction, never a raw secret. The stderr pipe is drained from a
  thread while stdout is read, so a chatty child cannot fill the pipe and stall the
  handshake until the timeout. Each kept line is capped so one enormous line cannot flood
  the summary.
- **Unreleased-tree warning.** When the running installer itself comes from a local source
  tree (`importlib.metadata` records a `file://` URL in `direct_url.json`: an editable
  install or any other local source install) and the default PyPI pin is being written, the
  installer prints a note naming the pinned version, saying the pinned PyPI build does not
  include the local changes, and pointing to `--from-checkout`. A PyPI wheel install carries
  no such record and prints nothing new.
- **CI guard.** The `smoke` job runs `scripts/ci_old_python_entry.py` after
  `actions/setup-python` has put Python 3.10 first on PATH. The guard builds the wheel,
  requires the plain (request-less) form to fail on that interpreter, then installs through
  the installer exactly as a user would and requires the installer's own post-write verify
  handshake to pass against the written entry. The failure class F2 cannot return silently.

## Consequences

- Out-of-the-box installs work on machines whose only Python is older than 3.12, at the
  price of a one-time managed-Python download where no qualifying interpreter exists.
- A failed post-write check reports the child's own words; the operator no longer reruns the
  command by hand to learn why.
- During development, an install from a tree whose version is not yet published says so
  instead of silently verifying the stale published build — the check stays honest about
  what it exercised.
- The smoke job downloads a managed Python and the wheel's dependencies on every run; it is
  the slow job's last step and stays there.

## Amendment (2026-11-19): verify response envelopes and scrub launch diagnostics

- The verifier accepts JSON-RPC 2.0 response envelopes with a matching integer ID and
  exactly one of `result` or `error`. Requests and notifications do not complete a check.
  Every exit cleans up the process group started for verification.
- The verifier polls both pipes. It uses the settings and stored-key secret inventory,
  prompted values, and credential-literal detection to scrub stderr before truncation.
  The diagnostic keeps at most five lines of 200 bytes each. A line beyond the bounded
  capture limit is omitted altogether.

## Amendment (2026-11-19): Strict JSON for launch metadata and replies

The installer omits the optional local-checkout warning when `direct_url.json` has
invalid UTF-8, excessive nesting, or invalid JSON. The verifier reads UTF-8 JSON-RPC
lines, allowing a UTF-8 BOM. It skips malformed lines, including invalid UTF-8,
excessive nesting, and nonstandard JSON constants, and still requires valid response
envelopes to complete the check.

## Amendment (2026-11-20): the guard's plain form sees only the old interpreter

uv 0.12 re-resolves an extra-free tool requirement against another interpreter it can find or
download, so with the `typesafe` extra gone (ADR-0084) the plain form started on the image's
Python 3.12 and the guard's control step passed for the wrong reason. The plain form now runs
with only the old interpreter's directory on PATH and `UV_PYTHON_DOWNLOADS=never`, and must
fail with the F2 error itself (`does not satisfy Python`). The installer step is unchanged.
