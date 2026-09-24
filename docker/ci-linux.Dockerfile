# syntax=docker/dockerfile:1
# The Linux CI image for `make ci-linux` and the pre-push hook's Linux leg (ADR-0056).
#
# Everything the GitHub `ci` workflow's runners provide is baked or cached here: the Node
# 24.19.0 the parity stage grounds JS behavior with, uv for `uv sync`/`uv build`/`uvx`, a
# uv-managed Python 3.12 so `uv sync` never downloads an interpreter at check time, and the
# system tools the tests shell out to (git, make, ps/procps). The base and the uv binaries
# are pinned by digest; tests/unit/test_ci_prepush_coverage.py asserts the pins stay.
#
# The check runs as the non-root `node` user (uid 1000, shipped by the base image), like
# GitHub's runner: permission behavior differs under root, and scripts/ci/linux_check.sh
# drops to this user before any stage runs.
#
# Caches live in /home/node/.cache/uv and /home/node/.local/share/uv; scripts/ci/
# linux_check.sh mounts named volumes there so repeated checks do not re-download wheels or
# interpreters.
FROM node:24.19.0-bookworm@sha256:4196d66a565c6f195728d9952f161f4adfe2ad753052a08b7ec7f1c5a6bda42b

# The uv release the repository develops with (uv.lock's creator), copied from the official
# image rather than fetched by an installer script, so the bytes come from the pinned digest.
COPY --from=ghcr.io/astral-sh/uv:0.9.24@sha256:816fdce3387ed2142e37d2e56e1b1b97ccc1ea87731ba199dc8a25c04e4997c5 /uv /uvx /usr/local/bin/

# git: the check runs against a real clone (identity.py reads .git directly, and parity's
# reference harness expects a git checkout). make and procps: the integration census and the
# support harness shell out to them, exactly as ubuntu-latest provides. A missing system
# tool is a gate failure, never a skipped test.
RUN apt-get update \
	&& apt-get install -y --no-install-recommends git make procps ca-certificates \
	&& rm -rf /var/lib/apt/lists/*

# Python 3.12 baked at build into the runner user's home, so `uv sync` in the container
# resolves immediately, offline.
ENV UV_PYTHON=3.12 \
	UV_CACHE_DIR=/home/node/.cache/uv \
	UV_PYTHON_INSTALL_DIR=/home/node/.local/share/uv
RUN mkdir -p /home/node/.cache/uv /home/node/.local/share/uv \
	&& chown -R node:node /home/node/.cache /home/node/.local \
	&& runuser -u node -- uv python install 3.12
