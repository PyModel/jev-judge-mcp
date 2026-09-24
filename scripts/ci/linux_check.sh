#!/usr/bin/env bash
# The Linux leg of the pre-push gate (ADR-0056): every command the GitHub `ci` workflow
# runs, with CI=true, inside a Linux image pinned by digest (docker/ci-linux.Dockerfile).
#
#   bash scripts/ci/linux_check.sh <source-dir> [label]
#
# The hook calls this against a clean temporary clone of the pushed commit; `make ci-linux`
# calls it against the working tree. The source directory is copied into a throwaway
# container and checked as the non-root `node` user, like GitHub's runner — nothing is
# written back to the source and the host .venv is untouched.
#
# The stage list mirrors .github/workflows/ci.yml job by job; drift in either direction
# fails tests/unit/test_ci_prepush_coverage.py, which runs inside the gate's own unit stage.
set -Eeuo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)
REPO_ROOT=$(cd "$SCRIPT_DIR/../.." && pwd -P)
DOCKERFILE="$REPO_ROOT/docker/ci-linux.Dockerfile"
IMAGE=jev-judge-mcp-ci-linux:adr0056
# Cache wheels and interpreters across checks; nothing else survives a check. They live in
# the runner user's home: the stages do not run as root.
CACHE_VOLUMES=(-v jev-judge-mcp-ci-uv-cache:/home/node/.cache/uv -v jev-judge-mcp-ci-uv-pythons:/home/node/.local/share/uv)

SRC=${1:?usage: linux_check.sh <source-dir> [label]}
SRC=$(cd "$SRC" && pwd -P)
LABEL=${2:-working-tree}

# Fail closed when Docker is not reachable: a push must never bypass the Linux leg by
# silently skipping it because the daemon is down or DOCKER_HOST is wrong.
if ! docker info >/dev/null 2>&1; then
	echo "pre-push: check 'linux' is blocked: docker is unreachable (daemon down or bad DOCKER_HOST); start Docker and rerun with: make ci-linux"
	exit 1
fi

# Build the digest-pinned image once; every later check reuses it.
if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
	echo "[pre-push] building $IMAGE from $DOCKERFILE (first run on this clone)"
	if ! docker build --quiet -f "$DOCKERFILE" "$(dirname "$DOCKERFILE")" -t "$IMAGE"; then
		echo "pre-push: check 'linux' is blocked: the CI image failed to build; rerun with: make ci-linux"
		exit 1
	fi
fi

# The stage runner ships with the machinery (this checkout's version), while every command
# it runs comes from the source being checked, exactly as GitHub runs the pushed commit's
# workflow. Runs in the container as /run_stages.sh.
RUNNER=$(mktemp "${TMPDIR:-/tmp}/jev-prepush-stages.XXXXXX")
LOG=$(mktemp "${TMPDIR:-/tmp}/jev-prepush-log.XXXXXX")
CID=""
cleanup() {
	# Always true: under bash 3.2 the EXIT trap's status would otherwise replace the
	# script's own (a clean run would exit 1).
	if [ -n "$CID" ]; then
		docker rm -f "$CID" >/dev/null 2>&1
	fi
	rm -f "$RUNNER" "$LOG"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
trap 'exit 129' HUP

cat >"$RUNNER" <<'RUNNER_EOF'
#!/usr/bin/env bash
# Runs inside the container as the non-root `node` user: the ci.yml command list, timed and
# bounded (ADR-0056).
set -Eeuo pipefail
cd /src
export CI=true
LABEL=${PREPUSH_LABEL:?}
STAGE_START=0

# A missing system tool the tests shell out to is a gate failure, not a skipped test: the
# image must provide what ubuntu-latest provides.
for tool in uv uvx git make ps node python3; do
	if ! command -v "$tool" >/dev/null 2>&1; then
		echo "pre-push: check 'linux:tools' failed for $LABEL: $tool is missing from the CI image; rerun with: make ci-linux"
		exit 1
	fi
done
echo "[pre-push] linux: user $(id -un) ($(id -u)); $(ps --version 2>&1); node $(node --version); git $(git --version | cut -d' ' -f3)"

# Generous but finite: a hung stage fails the check instead of wedging the push forever.
run() {
	local name=$1 seconds=$2
	shift 2
	STAGE_START=$(date +%s)
	echo "[pre-push] linux:$name ($LABEL)"
	if ! timeout "$seconds" "$@"; then
		echo "pre-push: check 'linux:$name' failed for $LABEL; rerun with: make ci-linux"
		exit 1
	fi
	echo "[pre-push] linux:$name ok in $(( $(date +%s) - STAGE_START ))s"
}

# uv sync first, as every ci.yml job does (stage names follow the workflow's jobs).
run sync 900 env -u TYPESAFE_API_KEY uv sync --locked --all-extras
run make:lint 600 make lint
run make:typecheck 1200 make typecheck
run make:unit 1800 make unit
run make:property 1200 make property
run make:policy-coverage 1200 make policy-coverage
run make:contract 1200 make contract
# actions/setup-node is pinned to 24.19.0, the parity oracle's version; emulate exactly.
NODE_VERSION=$(node --version)
if [ "$NODE_VERSION" != "v24.19.0" ]; then
	echo "pre-push: check 'linux:parity' failed for $LABEL: node is $NODE_VERSION, the workflow pins v24.19.0; rerun with: make ci-linux"
	exit 1
fi
run make:parity 1800 make parity
run make:security 1200 make security
run make:build 1200 make build
run make:smoke 3600 make smoke
run make:eval 1200 make eval
# The smoke job's last step: the installer-entry guard with Python 3.10 first on PATH
# (actions/setup-python 3.10), as ADR-0053's CI guard runs it. --no-project: inside the
# project, `uv python find 3.10` resolves the project's 3.12 instead of the managed 3.10.
run py310-install 900 env -u UV_PYTHON uv python install 3.10
PY310_BIN=$(dirname "$(env -u UV_PYTHON uv python find --no-project 3.10)")
echo "[pre-push] linux: old-python entry guard: $("$PY310_BIN/python3" --version) first on PATH"
run ci:old-python-entry 1800 env -u UV_PYTHON PATH="$PY310_BIN:$PATH" HOME=/home/node python3 scripts/ci_old_python_entry.py
echo "[pre-push] linux all stages ok ($LABEL)"
RUNNER_EOF

echo "[pre-push] linux ($LABEL): copying source into a throwaway container"
CID=$(docker create "${CACHE_VOLUMES[@]}" --entrypoint bash -e CI=true -e "PREPUSH_LABEL=$LABEL" "$IMAGE" -c 'chown -R node:node /src && export HOME=/home/node && exec runuser -u node -- /run_stages.sh')
docker cp "$SRC" "$CID:/src"
docker cp "$RUNNER" "$CID:/run_stages.sh"
set +e
docker start -a "$CID" 2>&1 | tee "$LOG"
STATUS=${PIPESTATUS[0]}
set -e
if [ "$STATUS" -ne 0 ]; then
	# A stage failure already printed its own naming line from inside the container; add one
	# only when the stages never ran (the container itself could not start).
	if ! grep -q "^pre-push: check 'linux:" "$LOG"; then
		echo "pre-push: check 'linux' failed for $LABEL (the container could not run the stages); rerun with: make ci-linux"
	fi
	exit "$STATUS"
fi
