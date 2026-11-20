---
status: accepted
---

# Release-PR CI is maintainer-approved

release-please opens and updates the release pull request with the workflow token.
GitHub creates an approval-required workflow run for the `opened`, `synchronize` and
`reopened` pull request events. A maintainer with write access can select **Approve workflows to run**
in the merge box to start CI on those changes. [GitHub token behavior](https://docs.github.com/en/actions/concepts/security/github_token)

We considered a GitHub App installation token for automatic pull request checks (option A).
The previous `release-pr-ci` job dispatched `ci.yml` on the release branch separately.

## Decision

Maintainers approve release CI using that pending run (option B). The `release-pr-ci`
job in `release.yml` is removed, along with the `workflow_dispatch` trigger on `ci.yml` that
existed only for it. The approved checks report on the release pull request, and
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

The build and wheel installation run on separate runners with read-only repository contents. The trusted GitHub
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

## Amendment 2026-11-19: attest distributions and verify the locked runtime

The build job grants `id-token: write` and `attestations: write` to record GitHub build
provenance for the wheel and source distribution after hashing them. Wheel verification keeps
read-only credentials. It installs the base runtime from the tagged commit's `uv.lock` with
hash enforcement, then installs the staged wheel with dependency resolution disabled. GitHub
stores the build provenance separately from the distributions sent to PyPI.
