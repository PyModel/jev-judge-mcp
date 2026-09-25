# CI stages, in ROADMAP order. Every declared stage is real: pytest exits 5 (nothing collected)
# when its directory is missing or empty, so a stage cannot pass vacuously. A new stage joins `ci`
# in the same commit that adds its tests.
PYTEST := uv run pytest

.PHONY: ci lint typecheck unit property policy-coverage contract parity security build smoke eval eval-live security-live ab load hooks ci-linux

ci: lint typecheck unit property policy-coverage contract parity security build smoke eval

# The pre-push gate (ADR-0056). `make hooks` enables the gate for this clone and proves it
# is reachable — with empty stdin nothing is checked, but the gate's banner must appear. The
# forwarders live outside the tracked tree, so every worktree and every checked-out commit
# keeps its previous hooks. See scripts/ci/install_hooks.sh.
hooks:
	bash scripts/ci/install_hooks.sh

# The gate's Linux leg against the working tree (the hook runs it against the pushed commit's
# temporary clone instead). Needs Docker; the image is built once from a digest-pinned base.
ci-linux:
	bash scripts/ci/linux_check.sh .

lint:
	uv run ruff check
	uv run ruff format --check

typecheck:
	uv run pyright

unit:
	$(PYTEST) tests/unit --cov=jev_judge_mcp --cov-report=term-missing

property:
	$(PYTEST) tests/property

# ROADMAP P3: the policy package keeps 100% branch coverage from its unit and property tests alone.
policy-coverage:
	$(PYTEST) tests/unit tests/property -q --cov=jev_judge_mcp.policy --cov-branch --cov-fail-under=100 --cov-report=term-missing

contract:
	$(PYTEST) tests/contract

parity:
	$(PYTEST) tests/parity

# ROADMAP P6: the scripted adversary, offline. Real now, so an empty stage fails (pytest exit 5).
security:
	$(PYTEST) tests/security

# Live P6 gate against TypeSafe (paid, bounded; tests/security/test_live_typesafe.py). Fails without
# TYPESAFE_API_KEY. Not part of `make ci` or the CI security job.
security-live:
	$(PYTEST) tests/security -m live

build:
	uv build

# The integration tests, then the slow `uvx --from .` smoke test. A command-line `-m` replaces the addopts
# one, so the smoke line selects `smoke` alone and never re-admits `live` (ADR-0027).
smoke:
	$(PYTEST) tests/integration
	$(PYTEST) tests/integration -m smoke

# ROADMAP P9: local overhead at 1/4/16/32/64 concurrent calls, provider stubbed (tests/load/). Timing-based,
# so it stays out of `make ci`.
load:
	$(PYTEST) tests/load -m load -s

# ROADMAP P7: offline eval scorer and calibration tests. No network, no API key.
eval:
	$(PYTEST) tests/evals

# Live L3 smoke against TypeSafe (paid, bounded; evals/README.md). Fails without TYPESAFE_API_KEY.
eval-live:
	$(PYTEST) tests/evals -m live

# L4 agent outcome study, one agent per call (paid, capped per agent at 18 runs and 25 USD; evals/README.md
# § L4). Refuses unless the caller passes JEV_AB_LIVE=1 on the command line; this target never sets it.
# Refuses to resume a study recorded under another Jev revision, fixture, or agent setup.
AGENT ?= claude
ab:
	uv run python -m evals.ab.run --agent $(AGENT)

# Parity fixtures (ROADMAP P0). The pinned TypeScript reference is fetched into
# tests/parity/reference/ (gitignored) and run under Node 24.19.0.

NODE ?= $(HOME)/.nvm/versions/node/v24.19.0/bin/node
NPM ?= $(dir $(NODE))npm
PARITY := tests/parity
REFERENCE := $(PARITY)/reference

.PHONY: parity-reference parity-record parity-verify

# Fetch and build the reference at the pinned commit. Needs the network once;
# later runs reuse the checkout and are offline.
parity-reference:
	PATH="$(dir $(NODE)):$$PATH" NODE="$(NODE)" NPM="$(NPM)" $(PARITY)/harness/reference.sh $(REFERENCE)

# Regenerate tests/parity/fixtures/ from the pinned reference. Offline: the only
# provider is the local fake in harness/lib.mjs.
parity-record: parity-reference
	PATH="$(dir $(NODE)):$$PATH" $(NODE) $(PARITY)/harness/record.mjs

# Replay every committed fixture against the reference through the fake and
# require byte-identical tool results.
parity-verify: parity-reference
	PATH="$(dir $(NODE)):$$PATH" $(NODE) $(PARITY)/harness/replay.mjs
