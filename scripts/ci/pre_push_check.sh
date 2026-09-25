#!/usr/bin/env bash
# The pre-push gate (ADR-0056): run every command the GitHub `ci` workflow runs against a
# clean temporary clone of each pushed commit — natively (`make ci`) and on Linux (the
# digest-pinned container; the Linux leg comes from the pushed commit's own
# scripts/ci/linux_check.sh when it carries one). Any failure blocks the push.
#
# Enabled per clone with `make hooks` (scripts/ci/install_hooks.sh); git feeds
# `<local-ref> <local-sha> <remote-ref> <remote-sha>` lines on stdin.
#
# The machinery is this checkout's version, but the commands it runs come from the pushed
# commit's ci.yml and Makefile, exactly as GitHub runs the pushed commit's workflow. The
# working tree is never tested: uncommitted changes can neither hide nor cause a failure.
set -Eeuo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)

# The hook inherits git's environment; a nested checkout must not see it.
unset GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE GIT_OBJECT_DIRECTORY \
	GIT_ALTERNATE_OBJECT_DIRECTORIES GIT_COMMON_DIR GIT_NAMESPACE GIT_QUARANTINE_PATH

REPO_ROOT=$(git rev-parse --show-toplevel)
# Native stage bounds: generous but finite, so a hung test or sync fails the push instead of
# wedging it. JEV_PREPUSH_TIMEOUT overrides the per-stage bound in seconds — an operator knob
# for slow machines, and it can only make the gate stricter, never looser.
NATIVE_SYNC_TIMEOUT=${JEV_PREPUSH_TIMEOUT:-900}
NATIVE_CI_TIMEOUT=${JEV_PREPUSH_TIMEOUT:-3600}

# Portable watchdog: macOS has no timeout(1). The child's direct children are terminated
# first, then the child, so a killed make or uv does not leave the tree running.
run_bounded() {
	local seconds=$1 pid watchdog status=0
	shift
	"$@" &
	pid=$!
	# The watchdog's own output goes nowhere and its children die with it: an orphaned
	# sleep must not hold the caller's stdout pipe open after the stage ends.
	(
		sleep "$seconds"
		pkill -TERM -P "$pid" 2>/dev/null || true
		kill -TERM "$pid" 2>/dev/null
	) >/dev/null 2>&1 &
	watchdog=$!
	wait "$pid" || status=$?
	pkill -TERM -P "$watchdog" 2>/dev/null || true
	# The subshell may have already torn itself down (its pkill woke it the moment this
	# side's pkill killed its sleep); losing that race must not kill the gate (set -e).
	kill "$watchdog" 2>/dev/null || true
	wait "$watchdog" 2>/dev/null || true
	return "$status"
}

TMP_DIR=""
cleanup() {
	# Always true: under bash 3.2 the EXIT trap's status would otherwise replace the
	# script's own (a clean run would exit 1).
	if [ -n "$TMP_DIR" ]; then
		rm -rf "$TMP_DIR"
	fi
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
trap 'exit 129' HUP

# Distinct pushed commits, deduplicated portably (the hook may run under bash 3.2, macOS's
# default, which has no associative arrays); deletions (all-zero local sha) have nothing to
# check.
SEEN="|"
PENDING=""
while read -r local_ref local_sha remote_ref remote_sha; do
	[ -n "$local_sha" ] || continue
	case "$local_sha" in *[^0]*) ;; *) continue ;; esac
	case "$SEEN" in
		*"|$local_sha|"*) ;;
		*)
			SEEN="$SEEN$local_sha|"
			PENDING="$PENDING$local_sha"$'\n'
			;;
	esac
done

if [ -z "$PENDING" ]; then
	echo "[pre-push] no refs to check (empty stdin or deletions only)"
	exit 0
fi

for sha in $PENDING; do
	short=${sha:0:7}
	echo "[pre-push] checking $short (native + linux) against a clean temporary clone"

	# A real temporary clone, detached at exactly this commit: a real .git directory, as
	# actions/checkout gives CI (identity.py reads it), independent of this worktree.
	TMP_DIR=$(mktemp -d "${TMPDIR:-/tmp}/jev-prepush.XXXXXX")
	git clone --quiet --no-hardlinks "$REPO_ROOT" "$TMP_DIR/src"
	git -C "$TMP_DIR/src" checkout --quiet --detach "$sha"

	native_start=$(date +%s)
	echo "[pre-push] native:uv sync ($short)"
	if ! run_bounded "$NATIVE_SYNC_TIMEOUT" env -u TYPESAFE_API_KEY uv sync --locked --all-extras --directory "$TMP_DIR/src"; then
		echo "pre-push: check 'native:uv sync' timed out after ${NATIVE_SYNC_TIMEOUT}s or failed for $short; rerun with: env -u TYPESAFE_API_KEY make ci"
		exit 1
	fi
	echo "[pre-push] native:uv sync ok in $(( $(date +%s) - native_start ))s"
	native_start=$(date +%s)
	echo "[pre-push] native:make ci ($short)"
	run_bounded "$NATIVE_CI_TIMEOUT" env -u TYPESAFE_API_KEY make -C "$TMP_DIR/src" ci || ci_status=$?
	if [ "${ci_status:-0}" -ne 0 ]; then
		if [ "$ci_status" -eq 143 ]; then
			echo "pre-push: check 'native:make ci' timed out after ${NATIVE_CI_TIMEOUT}s for $short; rerun with: env -u TYPESAFE_API_KEY make ci"
		else
			echo "pre-push: check 'native:make ci' failed for $short; rerun with: env -u TYPESAFE_API_KEY make ci"
		fi
		exit 1
	fi
	echo "[pre-push] native:make ci ok in $(( $(date +%s) - native_start ))s"

	if [ -x "$TMP_DIR/src/scripts/ci/linux_check.sh" ]; then
		# GitHub runs the pushed commit's workflow; the gate runs the pushed commit's leg —
		# its stage list and its Dockerfile, not this checkout's.
		bash "$TMP_DIR/src/scripts/ci/linux_check.sh" "$TMP_DIR/src" "$short" || exit 1
	else
		# A commit that predates the gate carries no leg; this checkout's machinery fills in.
		echo "[pre-push] pushed commit has no linux leg; using this checkout's scripts/ci/linux_check.sh"
		bash "$SCRIPT_DIR/linux_check.sh" "$TMP_DIR/src" "$short" || exit 1
	fi

	rm -rf "$TMP_DIR"
	TMP_DIR=""
done

echo "[pre-push] all checks passed: $(echo "$PENDING" | sed 's/^/  /;s/\(......\).*/\1/' | tr '\n' ' ')"
