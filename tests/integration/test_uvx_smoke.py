"""`uvx --from . jev-judge-mcp` starts and initializes (slow: builds the package)."""

import importlib.metadata
import shutil

import pytest

from tests.support.stdio import PROTOCOL_VERSION, REPO_ROOT, StdioServer

pytestmark = pytest.mark.smoke


@pytest.mark.skipif(shutil.which("uvx") is None, reason="uvx not on PATH")
def test_uvx_from_source_initializes() -> None:
    with StdioServer(["uvx", "--from", str(REPO_ROOT), "jev-judge-mcp"]) as server:
        reply = server.initialize()
        server.close_stdin()
        returncode, stderr = server.wait(timeout=120)
    assert reply["result"]["serverInfo"]["name"] == "jev-mcp"
    assert reply["result"]["protocolVersion"] == PROTOCOL_VERSION
    # A wheel has no source-tree suffix, even when `uvx --from .` built it from a checkout (ADR-0054).
    assert reply["result"]["serverInfo"]["version"] == importlib.metadata.version("jev-judge-mcp")
    assert "+" not in reply["result"]["serverInfo"]["version"]
    assert returncode == 0, stderr
