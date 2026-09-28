---
status: accepted
---

# Live agent evals run confined: a broker sidecar, a capability, and a container with no credential

The 2026-09-27 D3 paired study was voided: the agents under test ran with the operator's full
filesystem and read the whole provider `auth.json`, the TypeSafe key, other worktrees, the primary
checkout, and one run read the gold answers (`evals/reports/d3-paired`, diagnosis (b)–(d)). The
follow-up fixes — scoped `auth.json`/`models.json`, the study keyfile, the escape canary, config
hygiene, the wheel-built venv — narrow the exposure but are not a boundary: a same-uid agent can
still read any file the operator can, and the sandbox keyfile sits inside the run's reach.

This is an eval-harness decision. It changes no MCP tool, schema, threshold, or frozen reference
behavior, so it is not a divergence and is not entered in `docs/reference/divergences.json`.

## Decision

Live agent eval runs (the A/B study; the bench when it next runs live) execute inside a
confinement boundary of three pieces, built by `evals/confinement/` and driven by
`evals.ab.run.live`:

1. **The broker sidecar.** A host-named process the harness starts with `docker run`: a
   stdlib-only Python process (`evals/confinement/broker.py`) that alone holds the credentials.
   The operator's `JEV_STUDY_KEY_FILE` (and, for the Claude arm, `JEV_CLAUDE_KEY_FILE`) is
   bind-mounted into it **read-only, directly, with no copy anywhere**; the pi arm's scoped file
   — the one provider entry's key, never the operator's whole `auth.json` — is the only written
   credential, in a 0700 run temp dir deleted at study end. The harness process itself reads the
   key values only to scan and scrub the kept records; it does hold them in memory for that, and
   every injected credential is in that scan, not only the TypeSafe key. The broker listens on a
   Unix socket in a per-run docker volume, injects the real credential into allowlisted requests
   itself, forces `Accept-Encoding: identity` and refuses an encoded response it cannot scan,
   strips method-override headers, strips or redacts any upstream echo of a credential before the
   response crosses back, and writes receipts (method, host, path, status, bytes; capability by
   hash prefix) that never contain a key or a token. It carries a mandatory self-bound
   deadline (`max_lifetime_s`): even a harness killed without cleanup loses its key mounts within
   hours, not forever. The allowlist is exact paths, not wildcards, and requires origin-form
   request paths — an absolute URI or dot-segment never crosses, so the credential cannot be
   re-aimed at another authority on an allowlisted host. The provider's base URL path prefix
   (`/zen/go/v1` for opencode-go) is kept and prepended to every forwarded path.
2. **The capability boundary.** The harness mints a random per-run token — not the key — whose
   grant file lives in a directory mounted read-only into the broker; the TTL is the run timeout
   plus a margin, and removing the grant file revokes it (the broker re-reads grants per request).
   Unknown, expired, and revoked tokens are refused; the agent container mounts only its own grant
   file, read-only, at `/run/capability.json`.
3. **The agent container.** `--network none`, `--cap-drop ALL`, `no-new-privileges`; the only
   mounts are the materialized task workdir (rw), the run's scratch (rw), the socket volume (ro),
   and the run's own grant file (ro). There is no host HOME, keychain, `auth.json`, other
   worktree, or primary checkout inside; the agent config carries only the placeholder
   (`PLACEHOLDER_KEY`), and in-container shims (`evals/confinement/shim.py`) forward HTTP on
   loopback to the broker's Unix socket, stripping every client credential header first. The Jev
   MCP server runs inside this container from the wheel and reaches TypeSafe only through the
   broker. The model provider crosses the same way: the container's scoped `auth.json`/`models.json`
   hold the placeholder and a base URL pointing at the provider shim.

The allowlist is explicit: TypeSafe is `POST /v1/systemone` on `api.typesafe.ai` and nothing else;
the model provider is pinned by host (any path, `POST`/`GET`). Every other destination, method,
and operation is refused and the refusal is logged without the key.

### Why the broker is a sidecar, not a macOS process

The first design mounted a macOS-host Unix socket into the container. That cannot work under
OrbStack: the socket file surfaces in the container (bind mounts carry the file type), but
`connect(2)` returns `ECONNREFUSED`, because the listener lives in the macOS kernel and the
connect happens in the Linux VM kernel. Verified 2026-09-28 against file mounts (`:ro`) and
directory mounts alike; it is fundamental to any VM-based Docker, not an OrbStack bug. The
alternatives were: a keyless TCP→UDS relay pod (keeps the key on macOS but puts TCP on the
macOS↔VM hop), plain TCP on an internal docker network (the brief's forbidden silent switch), and
`sandbox-exec` instead of containers (deprecated, weaker). The decision — firstmate's, recorded in
the task inbox — was the sidecar broker with the shared volume: no TCP anywhere between the agent
and the broker, and as a bonus the macOS harness process never holds the key in memory at all
(it reads the value only to scan and scrub the kept records).

## Enforcement and checks

- **`make ci` (no Docker)**: `tests/evals/test_confinement_broker.py` runs the real broker process
  against a local fake upstream and pins the allowlist, capability refusals (unknown, expired,
  revoked), placeholder stripping, echo redaction, the body cap, and that receipts never carry a
  key or token; `tests/evals/test_confinement_launch.py` pins that the launch spec carries no
  credential in env, mounts, or args, that the agent container is `--network none` with exactly
  the four run mounts, the grant's shape and TTL, the container canary (host paths flagged,
  container state and the placeholder not), and the scoped provider file/plumbing.
- **Docker-marked adversarial suite** (`make confinement-adversarial`, skips cleanly without a
  daemon — the Linux CI shape): with a fake key and fake upstreams, a script inside the agent
  container dumps the environment, walks `/proc/*/environ`, greps the filesystem for both key
  values, probes the known credential paths and the broker's own mounts, attempts direct egress,
  and attacks the broker with a disallowed destination, method, path, and a forged capability —
  every probe must fail or miss. The same container then drives the wheel-installed server
  through shim → broker → fake TypeSafe for one real `jev_decide`: the operation must succeed, the
  upstream must have seen exactly the injected key (never the placeholder), and the echoed key
  must come back redacted. A second test runs a stub agent through `run_confined` end to end and
  proves the records, transcript, workdir, and teardown contracts still hold.
- **The escape canary stays and gained a container boundary** (`container_boundary`): the
  transcript scan flags host paths in commands and tool results; reading the placeholder
  `auth.json` inside the container is not an escape, because no real credential file exists there.
- **The after-run scan** (`secret_scan`): after every live run the harness scans the run's kept
  records for the key value host-side; a hit fails the run (recorded as an escape) and the scrub
  still removes the value afterwards.
- **Teardown**: on any exit path — success, timeout, crash — both containers are removed, the
  socket volume is deleted, and the grant files are gone with the run's temp root; the containers
  carry `jev-eval` labels and per-run name suffixes so cleanup never touches another lane.

## Consequences

- Live is confined-only. `JEV_AB_LIVE=1 make ab` builds the boundary and refuses (exit 2, nothing
  booked) when Docker, the agent image (`make confinement-image`), the key file, or the scoped
  provider credential is missing — there is no unconfined live path to fall back to. The macOS
  sandbox runner (`evals.agent.run_agent`) remains the offline dry-run shape and the record
  contract both paths share.
- The Claude arm's macOS keychain login cannot cross the boundary, so that arm needs
  `JEV_CLAUDE_KEY_FILE` naming a file with the Anthropic key; without it the study refuses with
  that stated reason (the arm is dropped rather than the boundary weakened). The pi arm needs the
  model's provider entry (with `baseUrl`) in the operator's `models.json` and an API-key (not
  OAuth) `auth.json` entry for that provider; the D3 model `opencode-go/deepseek-v4.1-flash` has
  no operator entry today, so the confined re-run adds one as its setup step.
- The confined preflight is one allowlisted provider request through the real boundary
  (`probe_provider`): no spend, no agent run, and a dead broker, image, or credential refuses the
  batch before anything is booked.
- `held_constant`'s Jev text changed, so studies recorded before this ADR will not resume against
  it — intended: pairs must not span two setups, and this is a setup change.
- The image pins the agent CLIs (`docker/eval-agent.Dockerfile`); a study's `agent_version`
  records the host binary's version, and the image is the one that actually runs. The re-run task
  should verify the two agree or pin the image tag in the study meta.
- **Broker egress (the firstmate decision on F9, accepted):** the broker container's layer-3
  egress is the docker bridge and is NOT network-restricted — no internal network or egress proxy
  was built. The control is in-process: the allowlist is checked before any socket opens, paths
  must be origin-form and exactly listed, overrides are stripped, and encoded responses are
  refused, so the only sockets the broker opens are to allowlisted origins, verified by the
  adversarial suite (including a request that names an allowlisted host with a re-aiming path).
  Layer-3 egress filtering remains a named follow-up, not a claim.
- **The grading boundary (F1):** nothing the agent wrote executes or is followed on the host.
  The diff, the reference grading, and the acceptance grading run inside the same image
  (`--network none`, `--cap-drop ALL`, only a symlink-preserving copy of the tree, the grading
  code, and an output dir mounted); relay logs cross by a no-follow, regular-file-only copy; the
  graded tree is copied with `symlinks=True` on both sides of the container line.
- **The pi arm's adapter ships in the image** (F2, pinned in `docker/eval-agent.Dockerfile` at
  `/usr/local/lib/node_modules/pi-mcp-adapter`): it is a package with its own dependency tree,
  and the arm loads it from that fixed path. The Docker-marked suite proves pi starts confined
  and completes one prompt end to end through the broker.
- **Teardown under signals (F5):** `main` turns SIGTERM into SystemExit so the context managers
  unwind; the broker self-expires; an owner-label reaper removes leftover containers, volumes,
  and stale run temp dirs at the next study start.
- **The bench (F11):** `JEV_BENCH_LIVE` live runs refuse until the bench is routed through
  `run_confined` — the named follow-up. There is no unconfined live path.
- The macOS `sandbox-exec` wrapper around the host-side launch is defence in depth and was
  deliberately not added.
- **Known information exposure (P3, documented rather than fixed):** `/proc/self/mountinfo`
  inside the agent container shows the host-side paths of its bind mounts (workdir, scratch,
  grant). No credential crosses; the paths are per-run temp dirs. Stripping mount sources is not
  cheap under docker, so it stands recorded here.
- **The D3 re-run shape:** `JEV_AB_MODEL=opencode-go/deepseek-v4.1-flash` selects the study model
  (the ds4 host-loopback default cannot cross); the operator's `models.json` needs the matching
  provider entry — `"opencode-go": {"baseUrl": "https://opencode.ai/zen/go/v1", "api":
  "openai-completions"}` — plus an API-key `auth.json` entry; the base path and exact endpoints
  ride the broker allowlist.
- Rotating the provider keys and the niblet token remains a separate credential step, unchanged.
