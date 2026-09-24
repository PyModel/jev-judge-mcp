---
status: accepted
---
# Report a checkout's build on the wire

Dogfood of 0.1.1 could not tell a PyPI install from an unreleased checkout: both answered
`initialize` with `serverInfo {"name":"jev-mcp","version":"0.1.1"}`. The operator had to compare
process start times. Sessions started before an update keep the old code until reconnect
(environment, not a server defect); the missing piece was a version that changes when the code does.

## Decision

`serverInfo.name` stays `jev-mcp`. ADR-0049 already pins it ("Names the TypeScript reference pins
stay: `serverInfo.name` `jev-mcp`"), and the frozen manifest records the same name
(`docs/reference/parity-manifest.json` `reference.server_info.name`). Renaming it would break wire
parity for a display problem the version can solve. This ADR does not reopen that name.

`serverInfo.version` is the installed distribution version, plus a PEP 440 local suffix when the
process is running from this distribution's git source tree and HEAD is not the commit named by
the tag `v<version>`:

- `<version>+g<7 hex chars of HEAD>`, for example `0.1.1+gc5fe9a1`.
- A wheel, a tree with no `.git`, a checkout whose HEAD commit is exactly that tag, and a HEAD
  that cannot be read, all report the plain version. No dirty-tree marker: the same commit is the
  same build.
- The git state is read from files. A worktree's `.git` file (`gitdir:`), its `commondir`, loose
  refs, and `packed-refs` (including a `^` peel) are followed. A loose annotated tag is peeled by
  reading the loose object. Nothing spawns `git`.
- A module path under `site-packages` or `dist-packages` is a wheel even when the venv sits inside
  the checkout, so `uvx --from .` reports the plain version.
- The same version string is logged once at startup (`identity jev-mcp <version>`) and printed by
  `jev-judge-mcp --version` (`jev-judge-mcp <version>`). `--version` does not start the server and
  does not read settings.

This is a wire divergence from the reference, which reports its own package version with no local
suffix (`checkout-version-suffix`). The reference has no checkout-versus-release distinction to
preserve; the suffix is how an operator tells the builds apart.

## Consequences

- CI and `uv run` from a checkout not sitting on `v<version>` report the suffix. A tag build and a
  wheel do not.
- Parity of `serverInfo.name` is unchanged. No recorded fixture compares `serverInfo.version`.
- The suffix is not a release version. release-please still owns `pyproject.toml` and the changelog;
  this ADR does not bump either.
