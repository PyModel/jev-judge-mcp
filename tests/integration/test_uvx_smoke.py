"""`uvx --from . jev-judge-mcp` starts and initializes (slow: builds the package)."""

import importlib.metadata
import shutil
from pathlib import Path

import pytest

from tests.support.stdio import PROTOCOL_VERSION, REPO_ROOT, StdioServer

pytestmark = pytest.mark.smoke


@pytest.mark.skipif(shutil.which("uvx") is None, reason="uvx not on PATH")
def test_uvx_from_source_initializes(tmp_path: Path) -> None:
    # cwd is not the checkout: a server that reads the skill off the source tree fails here.
    with StdioServer(["uvx", "--from", str(REPO_ROOT), "jev-judge-mcp"], cwd=tmp_path) as server:
        reply = server.initialize()
        skill = server.request(
            {
                "jsonrpc": "2.0",
                "id": 2,
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
    assert skill["result"]["contents"][0]["text"].startswith("---\nname: jev\n")
    assert returncode == 0, stderr
