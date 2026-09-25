---
status: accepted
---
# The pre-push gate: every ci.yml command, natively and on Linux, before any push

GitHub CI caught two failures that local `make ci` never saw: a unit test that passed on macOS
and failed on Linux runners (the TIME_WAIT port probe, ADR-0055's era), and an `eval` failure
keyed to path text that only the runner's layout produced. Nothing ran before a push — changes
landed on local `main` and were pushed from the local clone, so the first signal was always a
red run after the fact. This ADR puts the whole workflow in front of every push.

## Decision

- **Enabled per clone, reachable from every checkout.** `make hooks` runs
  `scripts/ci/install_hooks.sh`, which installs forwarders in the repository's common dir
  (`.git/jev-hooks`) — shared by every linked worktree — and points `core.hooksPath` there
  absolutely. Each forwarder delegates to the checked-out tree's `scripts/ci/hook_chain.sh`
  when the checkout carries it, and otherwise chains straight to the previous hooks, so no
  checkout of this repo — a worktree, an old commit, a bisect step, a hotfix off an old
  tag — is ever left with zero hooks, the machine's global commit-msg hooks included.
  `reference-transaction` is the one githooks(5) client hook not forwarded, on purpose: it
  fires on every ref transaction and a forwarder there costs one bash spawn per ref update
  for no gate value.
- **Reachability self-check, no bypass switch.** After installing the forwarders, the
  enablement runs `git hook run pre-push -- origin <url>` with empty stdin and fails unless
  the gate's banner appears. Nothing is checked with empty stdin, so the self-check costs
  nothing; a broken enable fails at `make hooks` instead of silently at push time. No flag
  or environment variable skips the checks.
- **The chain.** The repo-local `core.hooksPath` makes git stop consulting every other hook
  directory — on this project's machines, the global one that carries commit-msg hooks
  stripping AI attribution. Enabling the gate must not orphan those. The chain re-runs the
  hook that would have run without the repo-local setting: the global `core.hooksPath`, then
  the system one (each read with `--includes` so include directives expand and `--type path`
  so `~` does), else the repo's own `.git/hooks`. It never recurses into the enabled hooks
  directory or the legacy `.githooks` (real-path comparison), and an absent or
  non-executable previous hook is a no-op, exactly as git treats it. `pre-push` runs the
  gate first and then hands the previous pre-push the same saved stdin; either failing
  blocks the push. Previous hooks are executed directly — git executes hooks, shebang and
  all, and a python previous hook dies as bash-syntax noise when read as shell text.
- **The pushed commit, never the working tree.** For each distinct pushed commit (deletions
  skipped), the gate makes a real temporary `git clone` of the repository, detached at exactly
  that commit, and runs the checks there. A real clone has the `.git` directory
  `identity.py` reads (build identity on the wire, ADR-0054) — the reason `git worktree add`
  and tarballs are not enough. Uncommitted changes can neither hide a failure nor cause one:
  the check's verdict is a function of what would actually be pushed.
- **The machinery vs the commands.** The hook machinery runs from the checkout where the hook
  lives, but the commands it runs come from the pushed commit's `ci.yml` and `Makefile`,
  exactly as GitHub runs the pushed commit's workflow — including the Linux leg itself: a
  pushed commit that carries `scripts/ci/linux_check.sh` gets its own stage list and its own
  Dockerfile run against it, and only a commit that predates the gate falls back to the
  checkout's copy, with a printed notice. That is what lets the gate be validated against
  older commits: it ran dded642 (failing on Linux, as GitHub did) and 3281a99 (passing) from
  a later checkout.
- **Natively and on Linux.** The gate runs `make ci` natively (macOS or Linux, whatever the
  developer pushes from) and then the whole workflow again inside Linux: `scripts/ci/
  linux_check.sh` copies the source into a throwaway container from the digest-pinned image
  in `docker/ci-linux.Dockerfile` and runs, with `CI=true`, `uv sync --locked --all-extras`
  and every make stage the workflow's jobs run, plus the smoke job's old-Python entry guard
  (`scripts/ci_old_python_entry.py`) with a managed Python 3.10 first on PATH, found with
  `uv python find --no-project 3.10` — inside the project, a project-scoped find resolves the
  project's 3.12. `CI=true` enables no network or paid path GitHub CI does not have: its only
  two consumers are the HTTP auth suite's loopback-only relaxation (a local bind, not a
  network call) and the Node differential tests' skip-becomes-fail. No paid stage
  (`security-live`, `eval-live`, `ab`, `load`, benches) is in any leg and no live flag is set;
  `tests/unit/test_ci_prepush_coverage.py` holds that line.
- **Runner fidelity, learned the hard way.** The image ships Node 24.19.0 exactly (the
  workflow's `actions/setup-node` pin; a floating 24.x drifts ICU rendering) and asserts the
  version per check; git, make and procps, because the integration census and support harness
  shell out to them and a missing tool is a gate failure, never a skipped test; a non-root
  user (uid 1000), because the security wire tests deadlock under root; and docker's `--init`,
  because killed process groups in `tests/evals` otherwise linger as unreaped zombies and the
  group-death assertions see them alive. Each of these was a real leg failure that GitHub did
  not have, fixed by matching the runner instead of relaxing the gate.
- **Drift fails the build.** `tests/unit/test_ci_prepush_coverage.py` parses `ci.yml` and the
  machinery: every `uses:` step must be in the emulated set, every `run:` command covered by
  the native or Linux leg, the Linux stage list must equal the workflow's make targets both
  ways, every stage time-bounded, base images digest-pinned, and paid stages or live flags
  absent from the machinery. Unknown workflow shapes fail closed. A stage can only enter CI by
  teaching the gate in the same commit.
- **Bounded and honest.** Every stage runs under a generous but finite timeout — the Linux
  stages through `timeout(1)` in the container, the native sync and `make ci` through a
  portable watchdog (`JEV_PREPUSH_TIMEOUT` moves the bound for slow machines; it can only
  make the gate stricter) — so a hung test fails the push instead of wedging it. The
  container, its source copy and the temporary clone are removed on success, failure, and
  SIGINT/SIGTERM/SIGHUP. Any failure — a failing stage, an unreachable daemon, an image
  build that will not come up — blocks the push with one line naming the failed check and
  the command that reruns it (`make ci`, `make ci-linux`).
- **The container image is addressed by its recipe.** The image tag carries the Dockerfile's
  content hash, so a changed recipe (a Node bump, a new tool) builds a fresh image on the
  next check instead of a machine that already ran the gate checking every later push on a
  stale one.

## Consequences

- A push that would fail on Linux fails on the developer's machine first, at the cost of
  several minutes per push (the native leg is minutes; the Linux leg minutes more on a warm
  cache). Developers who want the Linux leg alone against the working tree run
  `make ci-linux`.
- The gate mirrors `ci.yml`, not replaces it: GitHub still runs on push. What the gate adds
  is the same signal before the push, plus one environment class GitHub cannot test (the
  developer's own OS).
- The `fix(install)` race this work also landed (the verify stderr tail built before the
  drain was joined) was found because the gate runs the same tests the workflow runs, in an
  environment whose scheduling differs just enough to expose a coin-flip.
- Hook chains are dynamic: whatever the global or system config points at when the hook runs
  is what chains, so changing the global hooks directory needs no re-run of `make hooks`.
