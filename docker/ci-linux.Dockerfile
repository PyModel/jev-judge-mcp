# syntax=docker/dockerfile:1
# The Linux CI image for `make ci-linux` and the pre-push hook's Linux leg (ADR-0056).
#
# Everything the GitHub `ci` workflow's runners provide is baked here: the Node 24.19.0 the
# parity stage grounds JS behavior with (actions/setup-node's exact version, checksum
# verified), the system tools the tests shell out to (git, make, ps/procps), and the uv the
# workflow's setup-uv step provides. The base is pinned by digest;
# tests/unit/test_ci_prepush_coverage.py asserts the pins stay.
#
# The check runs as the non-root `runner` user (uid 1000), like GitHub's runner: permission
# behavior differs under root, and scripts/ci/linux_check.sh drops to this user before any
# stage runs. Its uv caches live in /home/runner and are mounted as named volumes by
# scripts/ci/linux_check.sh, so repeated checks do not re-download wheels or interpreters.
FROM ghcr.io/astral-sh/uv@sha256:e5b65587bce7de595f299855d7385fe7fca39b8a74baa261ba1b7147afa78e58

# git: the check runs against a real clone (identity.py reads .git directly, and parity's
# reference harness expects a git checkout). make and procps: the integration census and the
# support harness shell out to them, exactly as ubuntu-latest provides. A missing system
# tool is a gate failure, never a skipped test. Node 24.19.0 lands in /usr/local from the
# official tarball, verified against the release SHASUMS256.
ARG TARGETARCH
RUN case "$TARGETARCH" in \
		amd64) NODEARCH=x64 ;; \
		arm64) NODEARCH=arm64 ;; \
		*) NODEARCH="$TARGETARCH" ;; \
	esac \
	&& apt-get update \
	&& apt-get install -y --no-install-recommends git make procps ca-certificates curl xz-utils \
	&& rm -rf /var/lib/apt/lists/* \
	&& curl -fsSLO "https://nodejs.org/dist/v24.19.0/node-v24.19.0-linux-${NODEARCH}.tar.xz" \
	&& curl -fsSLO "https://nodejs.org/dist/v24.19.0/SHASUMS256.txt" \
	&& grep " node-v24.19.0-linux-${NODEARCH}.tar.xz\$" SHASUMS256.txt | sha256sum -c - \
	&& tar -xJf "node-v24.19.0-linux-${NODEARCH}.tar.xz" -C /usr/local --strip-components=1 \
	&& rm "node-v24.19.0-linux-${NODEARCH}.tar.xz" SHASUMS256.txt \
	&& node --version

# The non-root runner and its uv caches; the managed interpreters (3.10 for the smoke job's
# old-Python entry guard) download into the mounted volume at check time.
ENV UV_PYTHON=3.12 \
	UV_CACHE_DIR=/home/runner/.cache/uv \
	UV_PYTHON_INSTALL_DIR=/home/runner/.local/share/uv
RUN useradd -m -u 1000 -s /bin/bash runner \
	&& mkdir -p /home/runner/.cache/uv /home/runner/.local/share/uv \
	&& chown -R runner:runner /home/runner/.cache /home/runner/.local
