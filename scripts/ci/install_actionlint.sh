#!/usr/bin/env bash
# Install the pinned actionlint for `make lint`'s workflow check (scripts/check_workflows.py).
#
# The pin is the CI image's (docker/ci-linux.Dockerfile): tests/unit/test_ci_prepush_coverage.py
# asserts the version and both per-arch sha256s stay equal between the two files, so this script
# and the Dockerfile are one pin in two places. No-ops when the pinned version is already on
# PATH — the gate's Linux image bakes it, so its mirror stage costs nothing there — and installs
# for real on GitHub's hosted runners, which carry no actionlint. Needs curl, sha256sum, tar.
set -Eeuo pipefail

VERSION=1.7.12

# The no-op check comes first: a machine of any kind that already carries the pinned binary
# (the gate's Linux image, a brew-installed Mac) installs nothing.
if command -v actionlint >/dev/null 2>&1 && [ "$(actionlint -version | head -1)" = "$VERSION" ]; then
	echo "install_actionlint.sh: actionlint $VERSION already on PATH"
	exit 0
fi

case "$(uname -m)" in
	x86_64) ARCH=amd64 ;;
	aarch64 | arm64) ARCH=arm64 ;;
	*) echo "install_actionlint.sh: unsupported machine $(uname -m)" >&2; exit 1 ;;
esac
case "$ARCH" in
	amd64) SHA=8aca8db96f1b94770f1b0d72b6dddcb1ebb8123cb3712530b08cc387b349a3d8 ;;
	arm64) SHA=325e971b6ba9bfa504672e29be93c24981eeb1c07576d730e9f7c8805afff0c6 ;;
	*) echo "install_actionlint.sh: unsupported arch $ARCH" >&2; exit 1 ;;
esac

dest=${1:-/usr/local/bin}
mkdir -p "$dest"
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
curl -fsSLo "$tmp/actionlint.tgz" "https://github.com/rhysd/actionlint/releases/download/v${VERSION}/actionlint_${VERSION}_linux_${ARCH}.tar.gz"
echo "${SHA}  $tmp/actionlint.tgz" | sha256sum -c -
tar -xzf "$tmp/actionlint.tgz" -C "$tmp" actionlint
if [ -w "$dest" ]; then
	install -m 0755 "$tmp/actionlint" "$dest/actionlint"
elif command -v sudo >/dev/null 2>&1; then
	sudo install -m 0755 "$tmp/actionlint" "$dest/actionlint"
else
	echo "install_actionlint.sh: $dest is not writable and sudo is unavailable; pass a writable dest" >&2
	exit 1
fi
if command -v actionlint >/dev/null 2>&1; then
	actionlint -version | head -1
else
	echo "install_actionlint.sh: installed to $dest/actionlint; put $dest on PATH" >&2
fi
