#!/usr/bin/env bash
# The Linux leg of the pre-push gate (ADR-0056): every command the GitHub `ci` workflow
# runs, with CI=true, inside a Linux image pinned by digest (docker/ci-linux.Dockerfile).
#
#   bash scripts/ci/linux_check.sh <source-dir> [label]
#
# The hook calls this against a clean temporary clone of the pushed commit; `make ci-linux`
# calls it against the working tree. The source directory is copied into a throwaway
# container and checked as the non-root `runner` user, like GitHub's runner — nothing is
# written back to the source and the host .venv is untouched.
#
# The stage list mirrors .github/workflows/ci.yml job by job; drift in either direction
# fails tests/unit/test_ci_prepush_coverage.py, which runs inside the gate's own unit stage.
set -Eeuo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)
REPO_ROOT=$(cd "$SCRIPT_DIR/../.." && pwd -P)
DOCKERFILE="$REPO_ROOT/docker/ci-linux.Dockerfile"
# The tag carries the Dockerfile's content hash: a changed image recipe (a Node bump, a new
# tool) builds a fresh image on the next check instead of reusing a stale one forever.
IMAGE=jev-judge-mcp-ci-linux:adr0056-$(cksum "$DOCKERFILE" | cut -d' ' -f1)
# Cache wheels and interpreters across checks; nothing else survives a check. They live in
# the runner user's home: the stages do not run as root.
CACHE_VOLUMES=(-v jev-judge-mcp-ci-uv-cache:/home/runner/.cache/uv -v jev-judge-mcp-ci-uv-pythons:/home/runner/.local/share/uv)

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
# Runs inside the container as the non-root `runner` user: the ci.yml command list, timed and
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
run ci:old-python-entry 1800 env -u UV_PYTHON PATH="$PY310_BIN:$PATH" HOME=/home/runner python3 scripts/ci_old_python_entry.py
echo "[pre-push] linux all stages ok ($LABEL)"
RUNNER_EOF

echo "[pre-push] linux ($LABEL): copying source into a throwaway container"
chmod 755 "$RUNNER"
# --init: the runner reaps orphans, as GitHub's environment does; without an init the
# killed agent groups in tests/evals linger as unreaped zombies and the group-death
# assertions see them alive.
CID=$(docker create --init "${CACHE_VOLUMES[@]}" --entrypoint bash -e CI=true -e "PREPUSH_LABEL=$LABEL" "$IMAGE" -c 'chmod 755 /run_stages.sh && chown -R runner:runner /src /home/runner/.cache/uv /home/runner/.local/share/uv && export HOME=/home/runner && exec runuser -u runner -- /run_stages.sh')
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

# ci.yml's security-one-cpu job, emulated faithfully: the whole security stage again under
# a one-CPU quota as a non-root runner in the same digest-pinned uv image the job pins,
# with the source bind-mounted read-only and copied inside, exactly as the job does.
echo "[pre-push] linux:security-one-cpu ($LABEL)"
onecpu_start=$(date +%s)
ONECPU_IMAGE=$(grep -o 'ghcr.io/astral-sh/uv@sha256:[a-f0-9]*' "$DOCKERFILE" | head -1)
if [ -z "$ONECPU_IMAGE" ]; then
	echo "pre-push: check 'linux:security-one-cpu' failed for $LABEL: the workflow's uv image digest is not pinned in $DOCKERFILE; rerun with: make ci-linux"
	exit 1
fi
cat >"$RUNNER.onecpu" <<'ONECPU_EOF'
#!/usr/bin/env bash
set -eux
apt-get update -qq && apt-get install -y -qq make git procps >/dev/null
useradd -m runner
cp -r /src /home/runner/app && chown -R runner:runner /home/runner/app
su runner -c "cd ~/app && uv sync --locked --all-extras && CI=true make security"
ONECPU_EOF
chmod 755 "$RUNNER.onecpu"
# Portable watchdog, same contract as the native leg's.
run_bounded() {
	local seconds=$1 pid watchdog status=0
	shift
	"$@" &
	pid=$!
	(
		sleep "$seconds"
		pkill -TERM -P "$pid" 2>/dev/null || true
		kill -TERM "$pid" 2>/dev/null
	) >/dev/null 2>&1 &
	watchdog=$!
	wait "$pid" || status=$?
	pkill -TERM -P "$watchdog" 2>/dev/null || true
	kill "$watchdog" 2>/dev/null
	wait "$watchdog" 2>/dev/null || true
	return "$status"
}
run_bounded 1800 docker run --rm --cpus 1 --name "jev-onecpu-$$" \
	--entrypoint sh -e CI=true -v "$SRC":/src:ro -v "$RUNNER.onecpu":/onecpu.sh:ro \
	"$ONECPU_IMAGE" /onecpu.sh
if [ $? -ne 0 ]; then
	echo "pre-push: check 'linux:security-one-cpu' failed for $LABEL; rerun with: make ci-linux"
	exit 1
fi
echo "[pre-push] linux:security-one-cpu ok in $(( $(date +%s) - onecpu_start ))s"
