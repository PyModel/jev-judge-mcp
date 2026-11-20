"""Give in-process unit tests their own key path, including when the host has a stored key."""

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolated_key_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    path = tmp_path / "config" / "key"
    monkeypatch.setenv("JEV_MCP_KEY_FILE", str(path))
    return path
