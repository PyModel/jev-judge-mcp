"""`uvx --no-cache --from . jev-judge-mcp` starts and initializes (slow: builds the package)."""

import importlib.metadata
import shutil
from pathlib import Path

import pytest

from tests.support.stdio import PROTOCOL_VERSION, REPO_ROOT, StdioServer

pytestmark = pytest.mark.smoke


@pytest.mark.skipif(shutil.which("uvx") is None, reason="uvx not on PATH")
def test_uvx_from_source_initializes(tmp_path: Path) -> None:
    # cwd is not the checkout: a server that reads the skill off the source tree fails here.
    # `--no-cache`: uv 0.9.x (local default, and the pinned Linux image) reuses a tool environment
    # for `uvx --from` a directory and ignores source edits and `--refresh-package`. cache-keys in
    # pyproject.toml cover a uv that honors them; this flag is what makes the smoke stage honest
    # on the uv that runs it.
    with StdioServer(["uvx", "--no-cache", "--from", str(REPO_ROOT), "jev-judge-mcp"], cwd=tmp_path) as server:
        reply = server.initialize()
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
