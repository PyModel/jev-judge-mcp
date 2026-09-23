"""The key file `jev-judge-mcp setup` writes and the resolver falls back to (ADR-0046)."""

import os
import stat
from pathlib import Path

import pytest

from jev_judge_mcp.keyfile import store_key, stored_key, stored_key_path
from jev_judge_mcp.settings import Settings, load_settings


@pytest.fixture(autouse=True)
def isolated_key_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    path = tmp_path / "config" / "key"
    monkeypatch.setenv("JEV_MCP_KEY_FILE", str(path))
    return path


def settings() -> Settings:
    return load_settings()


def test_round_trip(isolated_key_file: Path) -> None:
    stored = store_key(settings(), "sk-live-123")
    assert stored == isolated_key_file
    assert stored_key(settings()) == "sk-live-123"


def test_store_is_private(isolated_key_file: Path) -> None:
    store_key(settings(), "sk-live-123")
    mode = stat.S_IMODE(os.stat(isolated_key_file).st_mode)
    directory = stat.S_IMODE(os.stat(isolated_key_file.parent).st_mode)
    assert mode == 0o600
    assert directory == 0o700


def test_missing_or_blank_file_is_empty(isolated_key_file: Path) -> None:
    assert stored_key(settings()) == ""
    isolated_key_file.parent.mkdir(parents=True)
    isolated_key_file.write_text("   \n", encoding="utf-8")
    assert stored_key(settings()) == ""


def test_a_bom_in_the_file_never_becomes_part_of_the_key(isolated_key_file: Path) -> None:
    isolated_key_file.parent.mkdir(parents=True)
    isolated_key_file.write_bytes("\ufeffsk-live-123\n".encode())
    assert stored_key(settings()) == "sk-live-123"


def test_overwrite_replaces_the_old_key(isolated_key_file: Path) -> None:
    store_key(settings(), "first")
    store_key(settings(), "second")
    assert stored_key(settings()) == "second"
    assert isolated_key_file.read_text(encoding="utf-8") == "second\n"


def test_no_temp_file_survives_a_store(isolated_key_file: Path) -> None:
    store_key(settings(), "sk-live-123")
    assert [p.name for p in isolated_key_file.parent.iterdir()] == ["key"]


def test_a_filesystem_that_cannot_chmod_still_stores(monkeypatch: pytest.MonkeyPatch, isolated_key_file: Path) -> None:
    def refuse(path: object, mode: int) -> None:
        raise OSError("no mode bits on this filesystem")

    monkeypatch.setattr("jev_judge_mcp.fsutil.os.chmod", refuse)
    store_key(settings(), "sk-live-123")
    assert stored_key(settings()) == "sk-live-123"


def test_default_path_follows_xdg_config_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("JEV_MCP_KEY_FILE")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert stored_key_path(load_settings()) == tmp_path / "jev-mcp" / "key"
    monkeypatch.delenv("XDG_CONFIG_HOME")
    monkeypatch.setenv("HOME", str(tmp_path))
    assert stored_key_path(load_settings()) == tmp_path / ".config" / "jev-mcp" / "key"
