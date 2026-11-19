# Contributing

jev-judge-mcp is a Python MCP server exposing TypeSafe's Jev judgment tools,
frozen against a TypeScript reference server. The public repository is
https://github.com/PyModel/jev-judge-mcp. Open a pull request against `main`.
The maintainer, elkaix, approves every merge and every author, maintainer, or contributor addition.

## Read first

- `README.md`: what the server is, how to run it, and the recorded eval numbers.
- `docs/CONTEXT.md`: the project vocabulary.
- `docs/ROADMAP.md`: phases and acceptance criteria.
- `docs/adr/`: the decisions. The spec is frozen: a change the ADRs do not cover
  needs a new ADR, not a silent design choice.

## Set up

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```sh
uv sync --locked --all-extras
```

Development and `make typecheck` need every extra. `--extra typesafe` is only for running the
server: a plain or partial sync fails `make typecheck` with confusing `Import "typesafe_sdk"
could not be resolved` errors, and the same missing import fails the TypeSafe provider tests in
`make unit`, so a plain sync breaks two stages, not one.

## Prove a change

Run every CI stage locally, with `TYPESAFE_API_KEY` unset:

```sh
env -u TYPESAFE_API_KEY make ci
```

`make ci` must pass. The parity stage needs Node 24 and skips locally without it.
Workflow lint requires both Actionlint and ShellCheck on PATH. If Actionlint stalls,
the checker retries with the upstream pipe fix using Go 1.25+ when available.
A missing tool or an incomplete retry fails the gate. Report the command you ran
and its result with the change.

Load `.agents/skills/test-audit` before writing, changing, reviewing, or sweeping tests.

## The pre-push gate

Every push from an enabled clone runs the full gate first: `make ci` natively plus the whole
workflow again inside a Linux container, against a clean temporary clone of the pushed commit
— never your working tree. A failure blocks the push and names the check plus the command
that reruns it (`make ci`, or `make ci-linux` for the Linux leg alone against the working
tree). Expect a few minutes per push: roughly 4–5 minutes native, 5–15 minutes Linux on a
warm cache (the first run builds the image and fills the caches, so allow more).

Enable it once per clone:

```sh
make hooks
```

The setting is absolute, so a moved or renamed clone goes stale — git then runs no hooks at
all, with no warning. The next `make` in that clone fails with the repair instead: re-run
`make hooks` after moving a clone.

`make hooks` installs forwarders outside the tracked tree and points `core.hooksPath` at
them, then proves the gate is reachable (a banner check with empty stdin — nothing is
pushed, nothing is checked). Every checkout of the clone keeps its previous hooks: the
forwarders delegate to the checked-out gate when it has one and chain to your global or
system hooks otherwise. Docker must be running: an unreachable daemon blocks the push
rather than silently skipping the Linux leg. The gate mirrors `.github/workflows/ci.yml`;
the drift guard (`tests/unit/test_ci_prepush_coverage.py`) fails `make ci` if the workflow
and the gate ever diverge. Rationale and environment-fidelity notes:
`docs/adr/0056-pre-push-ci-gate.md`.

CI guards prevent these failure classes:

- The stale-hooks guard (`scripts/ci/check_hooks.sh`, run before every make target) fails the
  build when this clone's absolute `core.hooksPath` no longer exists or points into another
  repository — a moved or renamed clone — instead of letting every push run un-gated;
  `make hooks` re-enables. Fresh clones carry no local setting and are never blocked.

- The unit-stage fake-secret scanner rejects non-empty test credentials shorter than the server's
  eight-character redaction floor, preventing short values from corrupting unrelated text.
- The stdio integration census counts only exact extract-worker invocations descended from its own
  server, so unrelated agent sessions cannot fail worker-shutdown checks. A real cancellation test
  also verifies that the server stays responsive and cancellation leaves no new PPID-1 worker.
- The HTTP integration suite keeps local binds on loopback. On CI only, it binds `0.0.0.0` with a
  token and verifies unauthenticated requests get 401 while authenticated initialization gets 200.

- Fix a bug by first adding a test that fails, then making it pass.
- Never hand-edit `tests/parity/fixtures/`; change `tests/parity/cases/` and
  re-record with `make parity-record`.
- Never delete or weaken a test to make the build pass.

## Paid gates are not part of a contribution

`make security-live`, `make eval-live`, and `make ab` call the real TypeSafe API
and spend money. A normal contribution does not run them and must not require
them. They are run by the maintainer, within their budgets, when a change touches
live behavior.

## Offer a contribution

1. Branch from the current `main`.
2. Keep the change focused; one concern per commit, imperative subject line.
3. Rebase onto `main` so the branch fast-forwards.
4. Hand the branch to the maintainer with the `make ci` result.

## Releases

The Release workflow (`.github/workflows/release.yml`) opens a release pull
request from Conventional Commits on `main`. Merging it creates the draft
GitHub Release and its `v<version>` tag. A read-only job builds the tagged
commit with a hash-locked backend and stages the wheel, sdist and checksums in
an immutable Actions artifact. Another read-only job installs the staged wheel
in a fresh environment. The publisher uploads missing files, records
the verified checksums and compares every asset's name, upload state and SHA-256 digest
before and after GitHub publication. A failed check on the public assets stops automatic
PyPI upload. Inspect the public release against the saved receipt before distributing it.
ADR-0060 records the timing window and the release-please trust decision.

Before merging the release pull request, fill that release's entry in
[`docs/EVIDENCE.md`](docs/EVIDENCE.md): the certified operating points and any
regression from the latest recorded eval run, or an explicit line that no new
run was recorded since the previous entry. Recorded results only (`docs/evals/`,
`evals/reports/`), dated, with the pinned model; the page defines the format and
never requires a paid run.

On a merged release, `PYPI_PUBLISH=true` enables automatic PyPI upload after
GitHub publication. Leaving the repository variable unset or false holds that upload.

A manual run on `main` with a nonempty `republish_tag` authorizes a PyPI upload
**even when `PYPI_PUBLISH` is unset or false**. Use a published tag and an
unexpired verification receipt for that tag and commit. Supply the original
Release run ID in `verified_run`, or leave it blank for automatic lookup.
The workflow checks that the run belongs to this repository's Release workflow
on `main`, its verification and publication job succeeded, and the public
distributions match its receipt. Both paths require the `pypi` environment
and the PyPI project's trusted publisher for `PyModel/jev-judge-mcp`,
workflow `release.yml`, environment `pypi`.

- `.release-please-manifest.json` records the released version. A release pull
  request needs a releasable change such as a `feat` or `fix` commit in the scanned history.
- When release-please opens or updates the release pull request with the workflow token,
  GitHub creates a pending `ci` run. A maintainer with write access selects **Approve workflows to run**
  in the pull request's merge box (ADR-0060). If that run is unavailable, use
  `env -u TYPESAFE_API_KEY make ci` on the release branch as a fallback.
- The version gate is `scripts/check_release_version.py`, tested offline in
  `tests/unit/test_release_version.py`.

## License

The copyright holder is elkaix.

By contributing, you agree that your contributions are licensed under the MIT
License in `LICENSE`.
