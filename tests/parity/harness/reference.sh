#!/usr/bin/env bash
# Fetch the TypeScript reference server at the pinned commit into $1 and build it.
# The reference repo URL is not recorded here; set JEV_PARITY_REFERENCE_REPO to it.
set -euo pipefail

REPO=${JEV_PARITY_REFERENCE_REPO:?reference.sh: set JEV_PARITY_REFERENCE_REPO to the reference server git URL}
COMMIT=69ffb4b49c88802ec6e49b883f4a36b91d23197e
TREE=aa89f96564c7efe07c94535883de7cbad6af95b1
NODE_VERSION=v24.19.0
dir=$1

actual_node=$("$NODE" --version)
if [ "$actual_node" != "$NODE_VERSION" ]; then
  echo "reference.sh: need Node $NODE_VERSION, $NODE is $actual_node" >&2
  exit 1
fi

if [ ! -d "$dir/.git" ]; then
  git clone --quiet "$REPO" "$dir"
fi
if [ "$(git -C "$dir" rev-parse HEAD)" != "$COMMIT" ]; then
  git -C "$dir" checkout --quiet --detach "$COMMIT"
fi
if [ "$(git -C "$dir" rev-parse 'HEAD^{tree}')" != "$TREE" ]; then
  echo "reference.sh: $dir tree is not $TREE" >&2
  exit 1
fi
if [ -n "$(git -C "$dir" status --porcelain --untracked-files=no)" ]; then
  echo "reference.sh: $dir has local modifications" >&2
  exit 1
fi

stamp="$dir/.parity-built"
if [ ! -f "$stamp" ] || [ "$(cat "$stamp")" != "$COMMIT $NODE_VERSION" ]; then
  (cd "$dir" && "$NPM" ci --no-audit --no-fund --silent && "$NPM" run --silent build)
  echo "$COMMIT $NODE_VERSION" > "$stamp"
fi
