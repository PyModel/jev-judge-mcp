"""`uvx --no-cache --from . jev-judge-mcp` starts and initializes (slow: builds the package)."""

import importlib.metadata
import shutil
from pathlib import Path

import pytest

from jev_judge_mcp.install.launch import checkout_launch
from tests.support.stdio import PROTOCOL_VERSION, REPO_ROOT, StdioServer

pytestmark = pytest.mark.smoke

# `--no-cache`: uvx before 0.10.10 reuses a cached tool environment for `--from` a directory
# and ignores source edits, `--refresh`, and `--reinstall` (astral-sh/uv#18396); on every uv
# the cache-keys miss an uncommitted deletion. The temporary cache builds from this checkout.
_NO_CACHE = "--no-cache"

# The first reply must also cover `uvx --no-cache`'s cold build — resolve, ~5.6 MiB of wheels
# from PyPI, install (the stage "needs the network", Makefile) — measured 22-50 s on a slow
# link, far past the 15 s default hang budget. Later requests hit a warm server and keep it.
_COLD_BUILD_TIMEOUT = 240.0


def _assert_serves_this_checkout(command: list[str], tmp_path: Path) -> None:
    # cwd is not the checkout: a server that reads the skill off the source tree fails here.
    with StdioServer(command, cwd=tmp_path) as server:
        reply = server.initialize(timeout=_COLD_BUILD_TIMEOUT)
        listed = server.request({"jsonrpc": "2.0", "id": 2, "method": "resources/list", "params": {}})
        skill = server.request(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "resources/read",
                "params": {"uri": "jev-skill://jev/SKILL.md"},
            }
        )
        server.close_stdin()
        returncode, stderr = server.wait(timeout=120)
    assert reply["result"]["serverInfo"]["name"] == "jev-mcp"
    assert reply["result"]["protocolVersion"] == PROTOCOL_VERSION
    # A wheel has no source-tree suffix, even when `uvx --from .` built it from a checkout (ADR-0054).
    assert reply["result"]["serverInfo"]["version"] == importlib.metadata.version("jev-judge-mcp")
    assert "+" not in reply["result"]["serverInfo"]["version"]
    uris = {item["uri"] for item in listed["result"]["resources"]}
    assert "jev-skill://jev/SKILL.md" in uris
    assert "jev-skill://jev-mcp/SKILL.md" in uris
    # The served skill is this checkout's bytes: a stale cached build fails here instead of passing.
    assert skill["result"]["contents"][0]["text"] == (
        REPO_ROOT / "src" / "jev_judge_mcp" / "skills" / "jev" / "SKILL.md"
    ).read_text(encoding="utf-8")
    assert returncode == 0, stderr


@pytest.mark.skipif(shutil.which("uvx") is None, reason="uvx not on PATH")
def test_uvx_from_source_initializes(tmp_path: Path) -> None:
    _assert_serves_this_checkout(["uvx", _NO_CACHE, "--from", str(REPO_ROOT), "jev-judge-mcp"], tmp_path)


@pytest.mark.skipif(shutil.which("uvx") is None, reason="uvx not on PATH")
def test_uvx_from_checkout_launch_initializes(tmp_path: Path) -> None:
    """The spec `install --from-checkout` writes, still built with `--no-cache`."""
    launch = checkout_launch(shutil.which("uvx") or "uvx")
    _assert_serves_this_checkout([launch.uvx, _NO_CACHE, *launch.args()], tmp_path)
