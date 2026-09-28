---
status: accepted
---

# A selected provider that cannot answer any call refuses at startup

A bare `uvx --from <checkout> jev-judge-mcp` — the hand-registration form without the
`[typesafe]` extra — used to start, list tools, serve skills, and then fail **every** judgment
call typed `provider` with "install jev-judge-mcp[typesafe]". The server looked alive and was
useless for its purpose; the refusal arrived per call, after the client had connected and the
agent had committed to the tool.

## Decision

- `server.ensure_provider_runnable` runs in `main()` with the other startup gates, after
  `ensure_http_port_free` and before logging and serving. It resolves the provider once; when
  the selection is `typesafe` and `providers.typesafe.sdk_importable()` reports the SDK missing,
  the server exits with one stderr line (`provider_not_runnable_message`, which names the fix
  including the `[typesafe]` spec forms) before any client connects — the ADR-0050 shape: a
  misconfiguration is one clear line and a non-zero exit, never a half-working live server.
- The line against ADR-0008: provider **configuration** errors (unknown name, missing key, no
  credentials) stay per-call `ProviderConfigError`s exactly as the reference reports them
  (`provider.ts:35-77`); the gate catches them and returns. Only **runnability** — a selected
  provider whose transport package is not installed, a state the reference cannot reach (its
  SDK is built in) and no fixture covers — is a startup refusal.
- Scope is the server, both transports, after the dispatch of every subcommand: `install`,
  `hook`, `doctor`, `setup`, `calibrate`, `judge`, `gate`, and `completion-hook` never consult
  the gate. The CLI forms show the same one-line install hint immediately on their first call,
  which is the same failure made visible at once; only the server form used to hide it.
- A non-typesafe provider selected imports nothing extra: the probe is consulted only after
  resolution names `typesafe`, so an `openrouter`/`cloudflare`/`compatible` configuration is
  neither slower nor dependent on the SDK.
- `doctor` reports the SDK's importability as its own line (ADR-0046's offline-check role);
  it never exits on it.
- The README's hand-registration section states that the `[typesafe]` suffix is required and
  what its absence does.

## Consequences

- `tests/unit/test_provider_runnable.py` owns the gate: the refusal arm and the
  never-consults-the-probe canary patch `jev_judge_mcp.server.sdk_importable` (the binding the
  gate consults — `sys.modules` poisoning only holds in a process that never imported the SDK);
  the real missing-SDK boundary is a hermetic subprocess through `python -m jev_judge_mcp` with
  a shimmed `typesafe_sdk` that raises `ImportError`, asserting exit 1, the message, and no
  protocol bytes on stdout.
- `tests/unit/test_doctor.py` pins the doctor line's two arms.
- A future provider with an optional transport package joins the same gate: probe next to the
  provider, refusal before serving, per-call text unchanged.
