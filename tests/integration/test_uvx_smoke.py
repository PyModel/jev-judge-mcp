"""`uvx --from . jev-judge-mcp` starts and initializes (slow: builds the package)."""

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
    assert returncode == 0, stderr
