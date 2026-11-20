#!/usr/bin/env bash
# Install ripgrep for the jev_ask command suite (tests/security/test_ask_command.py), which runs
# the allowlisted `rg`. No-ops when `rg` is already on PATH: the gate's Linux image bakes it
# (docker/ci-linux.Dockerfile), so its mirror stage costs nothing there. GitHub's ubuntu-latest
# runner does not ship ripgrep, so the hosted security job installs it from apt.
set -Eeuo pipefail

if command -v rg >/dev/null 2>&1; then
	echo "install_ripgrep.sh: $(rg --version | head -1) already on PATH"
	exit 0
fi
sudo apt-get update -qq
sudo apt-get install -y -qq ripgrep >/dev/null
rg --version | head -1
