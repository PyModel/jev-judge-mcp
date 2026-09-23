---
status: accepted
---
# One contract test guards every paid path; runtime guards stay per runner

Three runners spend money, each behind its own flag:
- `evals/runners/live.py` behind `JEV_EVAL_LIVE`;
- `evals/ab/run.py` behind `JEV_AB_LIVE`;
- `evals/bench/run.py` behind `JEV_BENCH_LIVE`.

Two pytest targets run the `live` marker: `make eval-live` and `make security-live`.

The review found holes in how these are guarded:
- **Marker override.** Any `-m` on the command line replaces the `-m` in `addopts`. `make smoke`
  runs `-m "smoke or not smoke"` in CI, which is a tautology: it re-admits the `live` marker
  (collect-only showed 7 live tests collected). It is safe today only because
  `tests/integration` has no live test.
- **Weak recipe tests.** The three "no recipe or CI job sets the flag" tests scan only
  tab-prefixed Makefile lines and `ci.yml`. A target-specific or global `export`, or `release.yml`,
  passes all three. One test also passes on an empty Makefile, and another hardcodes the flag
  name.
- **No test keeps `eval-live` out of `ci:`.**
- **Unpinned provider.** `runners/live` never pins `JEV_PROVIDER=typesafe`, so a direct run could
  auto-resolve to another provider.

## Decision

- **The three runtime guards stay as they are.** Their messages, exception types and extra
  preconditions (request cap, pinned model, frozen labels) differ legitimately. A shared
  `require_paid()` would be shallow: its interface would be almost as large as its
  implementation.
- **`tests/contract/test_paid_gates.py` runs under `make ci`:**
  - `PAID_FLAGS` imports the three runners' `LIVE_FLAG` constants.
  - A parametrized test asserts the Makefile is non-empty and that no flag appears anywhere in the
    Makefile or in any `.github/workflows/*.yml`.
  - A second test asserts every `-m` expression in the Makefile other than `-m live` excludes
    `live`, and that `ci:` names no paid target.
- The per-module copies of the recipe test, and the recipe half of `tests/security/test_gates.py`,
  are removed. Each runner keeps its own refusal test.
- `make smoke` selects `-m smoke`.
- `runners/live` refuses unless the resolved provider is `typesafe`, matching `evals/README.md`.

## Consequences

- A new paid runner adds its flag to `PAID_FLAGS` and is covered at once.
- A test file marked `live` in any suite cannot reach CI through a marker expression.

As built: `tests/contract/test_paid_gates.py` holds the parametrized flag guard (comment lines in the
Makefile are skipped, since the `ab` target's comment names its flag), the `-m` check (a pytest recipe's
expression other than `live` must deselect a test marked only `live`, read by pytest's own expression
parser), and the paid-target check over `ci:` and every workflow's `make` lines. `make smoke` runs the
integration tests under addopts, then `-m smoke`, so the non-smoke integration tests stay in CI.
`runners/live` refuses unless `JEV_PROVIDER` is `typesafe`, before any case is loaded.
