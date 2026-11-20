"""The key file `jev-judge-mcp setup` writes and the resolver falls back to (ADR-0046)."""

import os
import stat
from pathlib import Path

import pytest

from jev_judge_mcp.keyfile import KEY_FILE_BYTES_MAX, redaction_values, store_key, stored_key, stored_key_path
from jev_judge_mcp.settings import Settings, load_settings


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


def test_an_undecodable_or_oversized_key_file_reads_as_empty(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The docstring's promise — never an error — holds for a non-UTF-8 store and for a store that
    is not a key at all (a device, a dump): both read as empty instead of a traceback or a hang."""
    path = tmp_path / "key"
    monkeypatch.setenv("JEV_MCP_KEY_FILE", str(path))
    path.write_bytes(b"\xff\xfe\x00k\x00e\x00y")
    assert stored_key(load_settings()) == ""
    path.write_bytes(b"k" * (KEY_FILE_BYTES_MAX + 1))
    assert stored_key(load_settings()) == ""
    path.write_bytes(b"k" * KEY_FILE_BYTES_MAX)
    assert stored_key(load_settings()) == "k" * KEY_FILE_BYTES_MAX


def test_the_redaction_set_is_the_configured_secrets_plus_the_stored_key(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path = tmp_path / "key"
    monkeypatch.setenv("JEV_MCP_KEY_FILE", str(path))
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    assert redaction_values(load_settings()) == load_settings().secret_values()
    path.write_text("sk-live-stored-0001\n", encoding="utf-8")
    values = redaction_values(load_settings())
    assert values[:-1] == load_settings().secret_values()
    assert values[-1] == "sk-live-stored-0001"


@pytest.mark.parametrize("directory", [False, True], ids=["character-device", "directory"])
def test_scout_010_descriptor_must_be_regular(
    isolated_key_file: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, directory: bool
) -> None:
    if directory:
        isolated_key_file.mkdir(parents=True)
    else:
        store_key(settings(), "fixture")
        real = isolated_key_file.stat()
        special = os.stat_result((stat.S_IFCHR | 0o600, *real[1:]))

        def describe(fd: int) -> os.stat_result:
            return special

        monkeypatch.setattr(os, "fstat", describe)
    assert stored_key(settings()) == ""
    assert str(isolated_key_file) in caplog.text and "not a regular file" in caplog.text


@pytest.mark.parametrize(
    ("accepted", "extra", "reason"),
    [
        pytest.param("x" * 4095, "x", "4096", id="ascii-byte-limit"),
        pytest.param("é" * 2047 + "x", "é", "4096", id="utf8-byte-limit"),
        pytest.param("existing-key", "\ud800", "valid UTF-8", id="invalid-utf8"),
    ],
)
def test_key_rotation_refuses_invalid_values_without_replacing_the_key(
    isolated_key_file: Path, accepted: str, extra: str, reason: str
) -> None:
    store_key(settings(), "  " + accepted + "  ")
    assert stored_key(settings()) == accepted
    before = isolated_key_file.stat()
    with pytest.raises(ValueError, match=reason):
        store_key(settings(), accepted + extra)
    assert isolated_key_file.stat().st_ino == before.st_ino
    assert stored_key(settings()) == accepted


@pytest.mark.parametrize(
    ("mode", "owner", "reason"),
    [
        pytest.param(0o460, "user", "group or other can write", id="group-writable"),
        pytest.param(0o442, "user", "group or other can write", id="other-writable"),
        pytest.param(0o444, "user", None, id="read-only"),
        pytest.param(0o400, "other", "owned by neither", id="different-owner"),
        pytest.param(0o444, "root", None, id="root-owned-read-only"),
        pytest.param(0o464, "root", "group or other can write", id="root-owned-group-writable"),
    ],
)
def test_key_read_requires_a_user_owned_descriptor_with_no_foreign_writers(
    isolated_key_file: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    mode: int,
    owner: str,
    reason: str | None,
) -> None:
    key = "test-key-content-8b73"
    loaded = settings()
    store_key(loaded, key)
    isolated_key_file.chmod(mode)
    if owner != "user":
        effective_uid = os.geteuid() or 1001
        monkeypatch.setattr(os, "geteuid", lambda: effective_uid)
        file_uid = 0 if owner == "root" else effective_uid + 1
        fstat = os.fstat
        expected_inode = isolated_key_file.stat().st_ino

        def describe(fd: int) -> os.stat_result:
            info = fstat(fd)
            if info.st_ino != expected_inode:
                return info
            return os.stat_result((*info[:4], file_uid, *info[5:]))

        monkeypatch.setattr(os, "fstat", describe)
    caplog.clear()
    assert stored_key(loaded) == (key if reason is None else "")
    if reason is None:
        assert not caplog.records
    else:
        assert stored_key(loaded) == ""
        (warning,) = caplog.records
        assert warning.levelname == "WARNING" and warning.name == "jev_judge_mcp.keyfile"
        assert str(isolated_key_file) in warning.message and reason in warning.message
        assert key not in warning.message
