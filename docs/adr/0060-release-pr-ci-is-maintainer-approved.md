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
(CONTRIBUTING.md § Releases). Both automatic PyPI upload and a manual retry with
`republish_tag` use the same `pypi` environment and OIDC publishing job.

## Release credentials and asset verification

We trust `googleapis/release-please-action@45996ed1f6d02564a971a2fa1b5860e934307cf7`
to create the release request, its labels, tag and draft. Any pin change requires
maintainer review of the upstream changes, bundled action code and required permissions.
The action is the only step in the version job. Its only credential is the job's
GitHub token, with write access to contents for tags and drafts, pull requests for
release changes, and issues for labels.

The build and wheel installation run on separate read-only runners. The trusted GitHub
publisher uploads the verified wheel, source distribution and checksum file from immutable
staging. It checks every attachment's name, completed upload state and SHA-256 digest
against its verified receipt immediately before and after publishing the same release ID.

GitHub checks the draft inventory and publishes it in separate requests. A concurrent
writer can change an attachment between those requests, and a changed attachment can be
public before the second check detects it. If the public inventory differs or cannot be
read, the publishing job fails and automatic PyPI upload stops. A maintainer must inspect
the public release and compare its attachments with the saved receipt before distributing it.
The check records the state GitHub returned at that time. Subsequent edits remain possible
on mutable releases. [GitHub release API](https://docs.github.com/en/rest/releases/releases#update-a-release)

## Consequences

- Nothing runs `ci` on the release pull request automatically; it merges on review, and the
  draft release's build/verify/publish stages stay the load-bearing gate after the merge.
- The repository's token surface shrinks: the job's `actions: write` permission and the App
  option's registration, rotation, and scoping all disappear.
- The pre-push gate's stage list (ADR-0056) mirrors `ci.yml`'s jobs, which are unchanged
  here, so its drift test stays green.
