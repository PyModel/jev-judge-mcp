# syntax=docker/dockerfile:1
# The confined agent runtime (ADR-0074). Built by `make confinement-image` with the freshly
# built wheel copied in; the study harness refuses to run live without it.
#
# Everything a live agent run needs inside the boundary: Python (the wheel-installed
# jev-judge-mcp plus the task fixtures' unittest runs), Node 24 and the pinned agent CLIs
# (pi with its TS extension loader, Claude Code), and git so an agent's ordinary workflows
# behave as they do on the host. No credential, config, or keychain is baked in: the agent
# container runs `--network none` with only the task workdir, its scratch, the broker's
# Unix socket, and its own capability grant mounted.
FROM python@sha256:f77ac9e44ae96ef2c90b8053ea08c31f8be030f824196b0ae4db6d462c84e51f

ARG TARGETARCH
RUN case "$TARGETARCH" in \
		amd64) NODEARCH=x64 ;; \
		arm64) NODEARCH=arm64 ;; \
		*) NODEARCH="$TARGETARCH" ;; \
	esac \
	&& apt-get update \
	&& apt-get install -y --no-install-recommends git ca-certificates curl xz-utils \
	&& rm -rf /var/lib/apt/lists/* \
	&& curl -fsSLO "https://nodejs.org/dist/v24.19.0/node-v24.19.0-linux-${NODEARCH}.tar.xz" \
	&& curl -fsSLO "https://nodejs.org/dist/v24.19.0/SHASUMS256.txt" \
	&& grep " node-v24.19.0-linux-${NODEARCH}.tar.xz\$" SHASUMS256.txt | sha256sum -c - \
	&& tar -xJf "node-v24.19.0-linux-${NODEARCH}.tar.xz" -C /usr/local --strip-components=1 \
	&& rm "node-v24.19.0-linux-${NODEARCH}.tar.xz" SHASUMS256.txt \
	&& node --version

# The agent CLIs, pinned so a study's agent_version means one binary. The pi MCP adapter ships
# here too (F2): it is a package with its own dependency tree, and the arm loads it from this
# fixed path — a lone index.ts copy cannot resolve its siblings or its node_modules.
ARG PI_VERSION=0.87.1
ARG CLAUDE_VERSION=2.1.283
ARG ADAPTER_VERSION=2.6.1
RUN npm install -g "@earendil-works/pi-coding-agent@${PI_VERSION}" "@anthropic-ai/claude-code@${CLAUDE_VERSION}" "pi-mcp-adapter@${ADAPTER_VERSION}" \
	&& pi --version && claude --version \
	&& node -e "require('fs').accessSync('/usr/local/lib/node_modules/pi-mcp-adapter/index.ts')"
ENV PI_MCP_ADAPTER=/usr/local/lib/node_modules/pi-mcp-adapter/index.ts

# The wheel is copied in by the build (make confinement-image builds it first), never fetched
# from an index: the server inside the container is exactly this checkout's revision.
COPY dist/jev_judge_mcp-*.whl /tmp/
RUN wheel=$(ls /tmp/jev_judge_mcp-*.whl) \
	&& pip install --no-cache-dir "$wheel[typesafe]" \
	&& rm /tmp/jev_judge_mcp-*.whl \
	&& python3 -c "import jev_judge_mcp; print(jev_judge_mcp.__file__)"
