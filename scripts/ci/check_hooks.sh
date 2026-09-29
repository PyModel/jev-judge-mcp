#!/usr/bin/env bash
# The stale-hooks guard (ADR-0056 amendment). `make hooks` writes an absolute
# core.hooksPath into this clone's local config; rename or move the clone and that path
# dangles, and git treats the missing hooks directory as "no hooks" — every commit and
# push then runs without the pre-push gate, with no warning. The Makefile runs this
# script before every target: a local core.hooksPath that does not exist, or that lives
# outside this repository, prints the one-line refusal (stdout — make turns it into a
# fatal error) and exits 1. A clone with no local core.hooksPath — a fresh clone, GitHub
# CI — exits silently: it behaves exactly as it did before the guard.
set -Eeuo pipefail

top=$(git rev-parse --show-toplevel 2>/dev/null) || exit 0
common=$(git rev-parse --git-common-dir 2>/dev/null) || exit 0
case "$common" in
	/*) ;;
	*) common="$top/$common" ;;
esac
# Read the local config file itself: no scope machinery, nothing inherited from the
# environment decides whether this clone's enablement is healthy.
p=$(git config --file "$common/config" --type path core.hooksPath 2>/dev/null) || p=""
[ -n "$p" ] || exit 0
case "$p" in
	/*) ;;
	*) p="$top/$p" ;;
esac
dir=$(cd "$p" 2>/dev/null && pwd -P) || {
	printf "hooks: core.hooksPath=%s does not exist — this clone moved or was renamed, and git runs no hooks at all, the pre-push gate included; rerun 'make hooks' to re-enable the pre-push gate\n" "$p"
	exit 1
}
top=$(cd "$top" && pwd -P)
common=$(cd "$common" && pwd -P)
case "$dir/" in
	"$top"/* | "$common"/*) exit 0 ;;
	*)
		printf "hooks: core.hooksPath=%s is outside this repository — it points into another repository; rerun 'make hooks' to re-enable the pre-push gate\n" "$p"
		exit 1
		;;
esac
