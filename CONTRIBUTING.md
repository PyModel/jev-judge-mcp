# Contributing

jev-judge-mcp is a Python MCP server exposing TypeSafe's Jev judgment tools,
frozen against a TypeScript reference server. The public repository is
https://github.com/PyModel/jev-judge-mcp. Open a pull request against `main`.

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
could not be resolved` errors.

## Prove a change

Run every CI stage locally, with `TYPESAFE_API_KEY` unset:

```sh
env -u TYPESAFE_API_KEY make ci
```

`make ci` must pass. The parity stage needs Node 24 and skips locally without it.
Report the command you ran and its result with the change.

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

The Release workflow (`.github/workflows/release.yml`) cuts releases from
Conventional Commits on `main`; there is no release trailer or manual tag. It
keeps a release pull request open with the next version and changelog; merging
it creates a draft GitHub Release and its `v<version>` tag. The workflow builds
that tag, uploads the wheel, sdist, and `SHA256SUMS.txt`, installs the downloaded
wheel into a fresh venv, and publishes the release only if the installed version
matches the tag. A failed check leaves the release a draft.

PyPI upload is held: the `pypi` job runs only when the repository variable
`PYPI_PUBLISH` is `true`, so until then a merged release pull request publishes
the GitHub Release only. Once the variable is set, the verified wheel and sdist
are uploaded to PyPI through trusted publishing: no token is stored in the
repository; the PyPI project (`jev-judge-mcp`) must have a pending or active
publisher for `PyModel/jev-judge-mcp`, workflow `release.yml`, environment `pypi`.
To upload a release that was cut while the hold was on, run the workflow by hand
with `republish_tag` set to its tag.

- The first release is `v0.1.0`. `release-please-config.json` pins it with `release-as`,
  which holds every later release pull request at `0.1.0`: delete that key in the first
  commit after the `v0.1.0` release merges. `.release-please-manifest.json` tracks the
  released version from then on. A release pull request needs a `feat` or `fix` commit in
  the scanned history.
- The release pull request is opened or updated with the workflow token, so its `pull_request`
  event does not trigger `ci`. After release-please updates it, `release.yml` dispatches
  `ci.yml` with `workflow_dispatch` on the release branch. GitHub documents that this
  token-triggered event is allowed and that selecting a ref runs on that branch ([event docs](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#workflow_dispatch),
  [manual dispatch docs](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/manually-run-a-workflow));
  the resulting checks attach to its head commit and report on the pull request ([run fields](https://docs.github.com/en/rest/actions/workflow-runs#get-a-workflow-run),
  [check runs](https://docs.github.com/en/rest/checks/runs)). If that dispatch or CI run is unavailable,
  use `env -u TYPESAFE_API_KEY make ci` on the release branch as a fallback.
- The version gate is `scripts/check_release_version.py`, tested offline in
  `tests/unit/test_release_version.py`.

## License

The copyright holder is Mohamed Elkholy.

By contributing, you agree that your contributions are licensed under the MIT
License in `LICENSE`.
