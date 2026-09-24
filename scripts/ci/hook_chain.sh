#!/usr/bin/env bash
# The hook chain (ADR-0056). `make hooks` points core.hooksPath at .githooks, which makes git
# stop consulting every other hook directory for this clone — including the global one this
# machine keeps its commit-msg hooks in. Every client-side hook name from githooks(5) there-
# fore lives in .githooks as a forwarder to this script, which re-runs the hook that would
# have run without the repo-local setting: the global core.hooksPath, then the system one,
# then the repo's own .git/hooks. It never recurses into .githooks itself, and a missing or
# non-executable previous hook is a no-op, exactly as git treats it.
#
# pre-push is special: the gate runs first — whether or not a previous pre-push exists — and
# the previous pre-push, if any, then sees the same saved stdin. Either one failing blocks
# the push.
set -Eeuo pipefail

name=${1:?usage: hook_chain.sh <hook-name> [args...]}
shift
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)
GITHOOKS_DIR=$(cd "$SCRIPT_DIR/../../.githooks" && pwd -P)

stdin_file=""
cleanup() {
	# Always true: under bash 3.2 the EXIT trap's status would otherwise replace the
	# script's own (a clean run would exit 1).
	if [ -n "$stdin_file" ]; then
		rm -f "$stdin_file"
	fi
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
trap 'exit 129' HUP

if [ "$name" = pre-push ]; then
	# The gate consumes the ref lines from stdin; save them so the previous pre-push sees
	# the same bytes. The gate's failure exits here and blocks the push before any
	# previous hook runs.
	stdin_file=$(mktemp "${TMPDIR:-/tmp}/jev-prepush-stdin.XXXXXX")
	cat >"$stdin_file"
	bash "$SCRIPT_DIR/pre_push_check.sh" <"$stdin_file"
fi

# The previous hooks path, resolved the way git resolves it minus the repo-local scope
# (command-line and repo config would only ever point back here). Global — which includes
# the XDG location — then system, each read with --includes so include directives expand
# and --type path so ~ expands. Default to the repo's own .git/hooks.
prev_dir=""
for scope in --global --system; do
	if p=$(git config --type path --includes "$scope" core.hooksPath 2>/dev/null) && [ -n "$p" ]; then
		prev_dir=$p
		break
	fi
done
if [ -z "$prev_dir" ]; then
	common=$(git rev-parse --git-common-dir)
	case "$common" in
		/*) ;;
		*) common="$(git rev-parse --show-toplevel)/$common" ;;
	esac
	prev_dir="$common/hooks"
fi
prev_dir=$(cd "$prev_dir" 2>/dev/null && pwd -P) || prev_dir=""

# Never recurse: if the previous path is this directory, the hook the chain would run is
# the chain itself. Git does not run an absent or non-executable hook, so neither does the
# chain.
if [ -z "$prev_dir" ] || [ "$prev_dir" = "$GITHOOKS_DIR" ]; then
	exit 0
fi
prev_hook=$prev_dir/$name
if [ ! -x "$prev_hook" ]; then
	exit 0
fi

if [ -n "$stdin_file" ]; then
	# A previous pre-push's failure falls through as this script's exit status and blocks
	# the push all the same.
	bash "$prev_hook" "$@" <"$stdin_file"
else
	exec bash "$prev_hook" "$@"
fi
