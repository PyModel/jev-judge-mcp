---
name: test-audit
description: "Invoke whenever writing, changing, reviewing, or sweeping tests in jev-judge-mcp. Authoring gate for new tests plus audit workflow for low-value, implementation-coupled, or duplicative tests and the test-only production seams they demand, under this repo's rule that a test is never deleted or weakened to make a failing build pass."
---

# Test Audit

Three modes, one value bar. Authoring mode gates every new or changed test at
write time. Audit mode runs focused sweeps of tests that re-assert source,
duplicate stronger proof, couple behavior to implementation, or keep test-only
production seams alive. Run broad audits as separate coherent follow-up
branches, one concern each; optimize for confidence, not deletion count.
Campaign mode prunes one whole test layer or source package at a time (see
Campaign below).

## Authoring gate

Before adding any test, answer four questions; a missing answer means do not
add it yet:

1. What observable behavior, invariant, or independent contract does it protect?
2. What credible regression makes it fail?
3. Why does existing coverage not already catch that failure? Each contract has
   one primary test owner at the strongest boundary; another layer needs its
   own distinct risk, such as a stdio/HTTP transport or lifecycle failure the
   owner cannot reach. Prefer extending a parametrized case or a shared helper
   in `tests/support/` over a near-duplicate test; consolidate duplicated
   setup in the same change (the ADR-0015 loader and ADR-0017 secret checks
   are the precedents).
4. Does it need a production seam (export, flag, wrapper, injection hook) that
   no production caller needs? If yes, move the test to the real boundary
   instead — see Test seams below.

Then check the test against every [junk pattern](#junk-patterns); a match fails
the gate unless the [retention bar](#retention-bar) names the contract it
independently guards. A test that would break under behavior-preserving
refactoring is asserting implementation, not behavior; rewrite it at the
owning boundary before landing it.

Bug regression tests must fail on the pre-fix code for the intended reason and
pass after the owner-boundary repair (CONTRIBUTING: "Fix a bug by first adding
a test that fails, then making it pass"). A regression test that never
demonstrably failed proves the mock, not the fix. One regression at the owner
boundary covers the bug; do not replay the same scenario at every layer it
crosses.

## Junk patterns

The shared checklist for both modes: the authoring gate rejects a new test that
matches one, and audits hunt for existing tests that do.

- assertion-free coverage probes;
- self-comparisons and identity copiers — expected output derived from the code
  under test (ADR-0017: coverage and echo checks must derive from the Settings
  schema independently, never by calling `secret_values()`);
- copied fixtures, inventories, manifests, or export lists — the hand-maintained
  three-place secret list ADR-0017 deleted is the canonical disease;
- exact source, import, or string greps;
- private predicate or call-shape tests duplicated at real boundaries;
- duplicate invocations of the same contract — the same fail-closed scenario
  re-asserted per tool when `tests/contract/test_fail_closed.py` owns it;
- provider-local replays of shared helpers — one shared contract suite per
  provider is the ROADMAP P4 rule; adapters may differ only in URL, auth, slug,
  envelope;
- tests whose only purpose is preserving test-only exports, globals, or wrappers;
- dead production code whose only callers are tests;
- expected values produced by the helper or renderer under test;
- mocks that implement the asserted behavior, or one identical mock standing in
  for different APIs — the parity fake speaks the compatible envelope and
  replays `{answers, usage, model}`; it never pre-cooks output text;
- fixtures that supply the ordering or behavior the owner should produce, or
  persistence asserted against a store the path never writes;
- capability tests that restate declared flags instead of exercising the
  delivery the flag promises — ADR-0027 deleted recipe tests that passed on an
  empty Makefile and missed `export` lines;
- negative controls that pass for an unrelated reason, such as a denial from a
  different guard or a rejection the production path never reaches;
- names or fixtures that promise more than the input exercises, such as a
  "refuses to pass vacuously" test that passes on an empty input;
- hand-edited parity expectations: `tests/parity/fixtures/` is recorded output,
  never a hand-maintained file — change `tests/parity/cases/` and re-record
  with `make parity-record`.

## Value bar

Tests justify their maintenance cost by protecting behavior, a credible
regression, or an independently meaningful contract. In an audit, an existing
test that must change for behavior-preserving source reorganization is suspect,
not automatically deletable; the authoring gate still rejects new ones.

Before judging a candidate, read the complete test and production owner, its
entry point, callers, callees, sibling implementations, overlapping tests, CI
routing, and relevant history. Read `docs/CONTEXT.md` (the vocabulary) and the
ADRs in `docs/adr/` first; the spec is frozen and a design the ADRs do not
cover needs a new ADR, not a silent test change. When the test claims
dependency-backed behavior (the `mcp` SDK, `typesafe-sdk`, `httpx`), inspect
the dependency source or types directly.

## Discovery

Keep discovery read-only and report evidence before editing. For broad scope,
run parallel discovery lanes when available:

- the core package `src/jev_judge_mcp/` (server, domain, policy, validation,
  providers, tools, extract, install);
- the test layers `tests/unit`, `tests/property`, `tests/contract`,
  `tests/parity`, `tests/security`, `tests/integration`, `tests/evals`,
  `tests/load`;
- the shared support and harness code `tests/support/` and
  `tests/parity/harness/`;
- a cross-cutting sweep of the spec surfaces: `docs/reference/parity-manifest.json`,
  `docs/reference/divergences.json`, `docs/reference/ts-0.5.0-tools-list.json`,
  and the ADR citations the doc-alignment suite guards
  (`tests/contract/test_docs_alignment.py`).

Outside campaign mode, prefer a few high-confidence candidates over a large
speculative inventory. Hunt for the [junk patterns](#junk-patterns).

## Retention bar

Keep a test when it independently enforces one of this repo's real contracts:

- **Parity with the Reference Implementation** (TS 0.5.0): byte-identical
  replay of the recorded fixtures under `tests/parity/fixtures/` (`make parity`,
  loader `tests/support/fixtures.py`, replay `tests/support/replay.py`,
  ADR-0015); every divergence registered in `docs/reference/divergences.json`
  and guarded by `tests/contract/test_divergence_registry.py` (ADR-0020,
  ADR-0024).
- **MCP wire bytes**: JS-compatible serialization with the Node 24 differential
  tests (ADR-0006), the frozen `tools/list` snapshot prefix
  (`tests/contract/test_tools_list.py`, `docs/reference/ts-0.5.0-tools-list.json`,
  ADR-0013/0048), schemas faithful to the argument validators at construction
  (ADR-0022), UTF-16 lengths (ADR-0005), a clean stdio stream, and build
  identity on the wire (ADR-0054).
- **Fail-closed policy**: a missing, malformed, or out-of-bounds answer never
  maps to `auto` (ADR-0002/0042; `tests/contract/test_fail_closed.py`, the
  policy property tests, and the 100% branch coverage `make policy-coverage`
  enforces on `jev_judge_mcp.policy`).
- **Secret redaction**: the secret set derives from the Settings schema and
  stays fail-closed in both directions (ADR-0008/0017;
  `tests/support/secrets.py`, `tests/unit/test_settings.py`, the provider echo
  matrix in `tests/contract/test_providers.py`).
- **Extract invariants**: an extracted value is always one of the verbatim
  regex candidates or null, and a pathological pattern cannot stall the server
  (ADR-0004/0018) — a violation is a release blocker, not a metric.
- **Installer and packaging**: `make build` plus the `uvx` smoke
  (`tests/integration/test_uvx_smoke.py`), installer entries that request a
  qualifying Python (ADR-0033/0053, `tests/unit/test_install.py`), and the
  release version gate (`scripts/check_release_version.py`,
  `tests/unit/test_release_version.py`).

Also keep:

- call ordering when order is observable behavior;
- regressions with a credible failure mode;
- source inspection when it is the cheapest independent guard: it fails when
  the contract changes (an ADR citation, a divergence id, a published schema)
  and survives an identifier-only refactor — `tests/contract/test_docs_alignment.py`
  is the model;
- a retained test that fails on the baseline: treat it as a possible product
  bug, reproduce it, and repair the owner rather than deleting it.

Static or slow is not a deletion reason; the `load` and `smoke` layers are slow
because they measure real overhead and spawn real processes. A test that
resembles implementation may still be the independent contract; prove otherwise
before removing it.

## Test seams

This repo keeps tests offline and deterministic by injecting time and
randomness at real boundaries, and that design is not clutter:

- Legitimate: an optional parameter on the production callable whose default
  **is** the production behavior. `ensure_http_port_free` takes
  `sleep=time.sleep, clock=time.monotonic` and the unit test passes a Clock
  that advances only when the code sleeps (ADR-0055,
  `tests/unit/test_http_port.py`). The eval harness passes `NO_RETRIES`
  through the provider's optional `retry` parameter while the server keeps
  its bounded ADR-0057 default. A fake that simulates inputs (the parity
  envelope fake, `respx`) at the real boundary is the same shape.
- A test-only production seam: an export, flag, environment variable, module
  global, or wrapper that exists only so a test can reach or alter behavior,
  with no production caller. ADR-0055 is explicit: "There is no retry
  environment variable: the budget is part of the startup contract, not
  operator configuration."

So a seam is legitimate when production callers use the same parameter with
their own values, and clutter when the parameter, flag, or export has exactly
one caller: the test. Do not flag an accepted injection point as clutter, and
do not bless a new test-only export — move the test to the real boundary.

## Candidate evidence

Record every field below before editing. A missing field means the candidate
is not ready for deletion:

- exact test name and location;
- what failure it can actually detect;
- non-test callers of the covered production or support seam;
- stronger remaining owner-boundary proof, or why no proof is needed;
- relevant history and the reason the test or seam exists;
- production or test-support deletion unlocked;
- risk and the focused validation command.

## Edit shape

Choose one coherent owner-boundary batch. Delete obsolete test-only exports,
globals, wrappers, and dead production paths instead of preserving aliases.
Move retained regressions to their canonical owners. Consolidate repeated
package or dependency assertions into one generic helper in `tests/support/`,
and keep that support dumb: one owner per shared concern, no speculative
capability (ADR-0015 rejected a fixture framework for exactly this reason).

Prefer net-negative production LOC. Do not add replacement tests that restate
the same implementation, and do not convert uncertain candidates into cleanup
to increase deletion counts.

Hard limits from CONTRIBUTING.md, in force in every mode:

- Never delete or weaken a test to make a failing build pass. Deletion happens
  only through a complete candidate-evidence record, in its own focused change
  — never inside a change that needs the build green.
- Never hand-edit `tests/parity/fixtures/`; change `tests/parity/cases/` and
  re-record with `make parity-record`.
- A divergence from the reference is a decision for `docs/reference/divergences.json`
  and an ADR, never a test edit alone.

## Campaign

One campaign covers exactly one scope — a single test layer (`tests/unit`,
`tests/property`, `tests/contract`, `tests/parity`, `tests/security`,
`tests/integration`, `tests/evals`, `tests/load`) or a single source package
(`policy`, `providers`, `validation`, `tools`, `extract`, `install`, `domain`).
Inventory the whole scope read-only first, record candidate evidence for every
deletion, and land each coherent batch as its own commit on its own branch;
rebase between batches so each stays a fast-forward.

## Validation

Run pytest targets sequentially; never edit source or tests while a pytest or
`make` run is active in the checkout. Paid gates (`make security-live`,
`make eval-live`, `make ab`) are maintainer-only and never part of a
contribution.

1. Run the smallest owner and sibling tests with the Makefile's runner, e.g.
   `uv run pytest tests/unit/test_http_port.py -q`; a whole layer through its
   target, e.g. `make unit` or `make contract`.
2. For parity-adjacent edits, re-record from the cases with `make
   parity-record`, then replay with `make parity-verify`. For registry or
   doc-claim edits, run their guards: `uv run pytest
   tests/contract/test_divergence_registry.py
   tests/contract/test_docs_alignment.py`.
3. Run targeted formatting, then `git diff --check`:
   `uv run ruff check && uv run ruff format --check`.
4. Classify and run the real gate: `env -u TYPESAFE_API_KEY make ci`, the
   command CONTRIBUTING.md requires. Parity and the differential tests need
   Node 24 and skip locally without it (`tests/support/node.py`) — a local
   skip is not proof; on CI they run and must pass.
5. Inspect `git diff --numstat`; report production/tooling separately from
   tests and test support.

## Landing and continuation

Land only as CONTRIBUTING.md's "Offer a contribution" describes: branch from
the current `main`, one concern per commit with an imperative subject, rebase
so the branch fast-forwards, and hand the branch to the maintainer with the
`env -u TYPESAFE_API_KEY make ci` result and the command output. Land one
coherent change at a time; after landing, refresh from `main` and rerun
read-only discovery for the next high-confidence batch.

## Handoff

Report:

- root cause and removed low-value categories;
- production owner simplifications;
- retained false positives and why they remain valuable;
- focused and full proof actually run, including which stages were local skips;
- production versus test LOC;
- branch, commit, and merge state;
- named follow-ups.
