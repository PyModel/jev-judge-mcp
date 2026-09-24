#!/usr/bin/env bash
# The pre-push gate (ADR-0056): run every command the GitHub `ci` workflow runs against a
# clean temporary clone of each pushed commit — natively (`make ci`) and on Linux (the
# digest-pinned container via scripts/ci/linux_check.sh). Any failure blocks the push.
#
# Installed per clone with `make hooks` (git config core.hooksPath .githooks); git feeds
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
	echo "[pre-push] native:make ci ($short)"
	if ! env -u TYPESAFE_API_KEY uv sync --locked --all-extras --directory "$TMP_DIR/src"; then
		echo "pre-push: check 'native:uv sync' failed for $short; rerun with: env -u TYPESAFE_API_KEY make ci"
		exit 1
	fi
	if ! env -u TYPESAFE_API_KEY make -C "$TMP_DIR/src" ci; then
		echo "pre-push: check 'native:make ci' failed for $short; rerun with: env -u TYPESAFE_API_KEY make ci"
		exit 1
	fi
	echo "[pre-push] native:make ci ok in $(( $(date +%s) - native_start ))s"

	if ! bash "$SCRIPT_DIR/linux_check.sh" "$TMP_DIR/src" "$short"; then
		exit 1
	fi

	rm -rf "$TMP_DIR"
	TMP_DIR=""
done

echo "[pre-push] all checks passed: $(echo "$PENDING" | sed 's/^/  /;s/\(......\).*/\1/' | tr '\n' ' ')"
