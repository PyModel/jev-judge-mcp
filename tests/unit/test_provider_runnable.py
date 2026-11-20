"""The stdio entry point starts with TypeSafe selected in a base install."""

from pathlib import Path

from tests.support.stdio import StdioServer


def test_the_server_entry_serves_typesafe_without_the_extra(tmp_path: Path) -> None:
    shim = tmp_path / "shim"
    shim.mkdir()
    (shim / "typesafe_sdk.py").write_text('raise ImportError("optional package unavailable")\n', encoding="utf-8")
    keyfile = tmp_path / "key"
    keyfile.write_text("typesafe-test-key-0001\n", encoding="utf-8")
    keyfile.chmod(0o600)
    env = {"JEV_PROVIDER": "typesafe", "JEV_MCP_KEY_FILE": str(keyfile), "PYTHONPATH": str(shim)}

    with StdioServer(env=env, cwd=tmp_path) as server:
        initialized = server.initialize()
        server.close_stdin()
        returncode, stderr = server.wait()
    assert initialized["result"]["serverInfo"]["name"] == "jev-mcp"
    assert returncode == 0, stderr
