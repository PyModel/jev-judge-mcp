---
status: accepted
---

# Release-PR CI is maintainer-approved

release-please opens and updates the release pull request with the workflow token, so its
`pull_request` event starts no `ci` run. Automating that run has two shapes: a GitHub App
whose token starts the pull request's own events (option A), or `release.yml` dispatching
`ci.yml` on the release branch through `workflow_dispatch` (what the `release-pr-ci` job
did). The dispatch cannot serve the purpose: GitHub documents that checks from such a run
never count toward the pull request's required checks
([troubleshooting required status checks](https://docs.github.com/en/pull-requests/collaborating-with-pull-requests/collaborating-on-repositories-with-code-quality-features/troubleshooting-required-status-checks)),
so the job was dead weight that looked like coverage.

## Decision

Release-PR CI is maintainer-approved (option B: no App, no dispatch). The `release-pr-ci`
job in `release.yml` is removed, along with the `workflow_dispatch` trigger on `ci.yml` that
existed only for it. A maintainer approves the pending `ci` run on the release PR; those
checks report on the pull request like any other branch's, and
`env -u TYPESAFE_API_KEY make ci` on the release branch remains the local fallback
(CONTRIBUTING.md § Releases). PyPI trusted publishing is untouched: the `pypi` and
`pypi-retry` jobs keep `id-token: write`, the `pypi` environment, and `pypi-retry`'s own
`workflow_dispatch` with `republish_tag`.

## Consequences

- Nothing runs `ci` on the release pull request automatically; it merges on review, and the
  draft release's build/verify/publish stages stay the load-bearing gate after the merge.
- The repository's token surface shrinks: the job's `actions: write` permission and the App
  option's registration, rotation, and scoping all disappear.
- The pre-push gate's stage list (ADR-0056) mirrors `ci.yml`'s jobs, which are unchanged
  here, so its drift test stays green.
