---
status: accepted
---

# After publication, install launches the version-pinned PyPI package by default

Supersedes the checkout pin in ADR-0033 ("Not published yet"). That pin was a pre-publication
bootstrap whose expiry condition passed silently: `jev-judge-mcp` 0.1.1 is on PyPI, and the
published wheel's `install` still refused to run anywhere but a clone, exiting with "refusing to
write a PyPI package spec before publication". Every entry it did write pinned the checkout's
absolute path, so moving or deleting the checkout silently broke the `jev` server in every
configured agent.

## Decision

- **Default.** `install` writes `--from jev-judge-mcp[typesafe]==<version>`, where `<version>` is
  the installed version of the running package from `importlib.metadata.version`. This holds from
  a wheel and from a checkout alike: outside a checkout the installer now works instead of
  exiting 1, and entries stop depending on a mutable path. If the distribution metadata is absent
  (raw sources, never installed), the default launch fails with that message and `--from-checkout`
  remains available.
- **`--from-checkout`.** An explicit flag writes `<absolute checkout>[typesafe]`, found the way
  ADR-0033 found it: walking up from the running package to a `pyproject.toml` that names
  `jev-judge-mcp`. With no checkout it fails with a message that says so. This is the development
  mode for an unreleased tree, not the default.
- **Spec shapes.** Validation accepts exactly the two shapes this installer writes — the
  version-pinned PyPI spec and the checkout spec — and refuses anything else (the bare package
  name, another project's name, a relative path) before any config is touched.
- **Migration.** No new command: re-running `install` already rewrites entries this installer owns
  (the state hash covers the written entry, not the spec form), so one plain re-run moves an
  existing fleet from checkout pins to the version-pinned PyPI spec. Entries the installer does
  not own stay untouched, as before; the same re-run upgrades a pinned entry when a new version is
  installed.
- **Verification network.** The post-write handshake runs the written command. For a
  PyPI-spec entry whose version is not cached, `uvx` may download the package from PyPI once —
  a deliberate network touch during a real install. Tests never do this: they mock or stub the
  launch, and the handshake itself is tested against a local script.

## Consequences

- The "before publication" error and the ADR-0033 bootstrap wording are gone; a wheel install of
  the released package is a first-class path.
- A configured agent no longer breaks when its checkout moves or is deleted; the pin names the
  published package, and `--remove` still removes entries of either shape.
- ADR-0033's remaining decisions (one named entry per agent, key handling, GUI file modes, Codex
  TOML, OpenCode JSONC, Pi adapter, Pythinker desktop) are unchanged.
