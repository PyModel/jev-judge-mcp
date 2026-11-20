"""`fsutil`: the one owner of 0600-in-0700 writes under the user's home (cache, key file)."""

import os
import stat
from pathlib import Path

import pytest

from jev_judge_mcp import fsutil
from jev_judge_mcp.settings import load_settings


def _mode(path: Path) -> int:
    return stat.S_IMODE(os.stat(path).st_mode)


def test_write_is_private_in_a_private_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "nested" / "jev-mcp" / "record.json"
    synced: list[int] = []
    fsync = os.fsync

    def observe(fd: int) -> None:
        info = os.fstat(fd)
        if stat.S_ISDIR(info.st_mode):
            synced.append(info.st_ino)
        fsync(fd)

    monkeypatch.setattr(os, "fsync", observe)
    fsutil.write_private_atomic(target, "{}")
    assert target.read_text(encoding="utf-8") == "{}"
    assert _mode(target) == 0o600
    assert _mode(target.parent) == 0o700
    assert synced == [path.stat().st_ino for path in (tmp_path, target.parent.parent, target.parent)]


def test_a_write_replaces_an_existing_loose_file_with_a_private_one(tmp_path: Path) -> None:
    target = tmp_path / "record.json"
    target.write_text("old", encoding="utf-8")
    os.chmod(target, 0o644)
    fsutil.write_private_atomic(target, "new")
    assert target.read_text(encoding="utf-8") == "new"
    assert _mode(target) == 0o600


def test_no_temporary_survives_a_failed_write(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def refuse(source: object, destination: object) -> None:
        raise OSError("disk gone")

    monkeypatch.setattr("jev_judge_mcp.fsutil.os.replace", refuse)
    target = tmp_path / "record.json"
    with pytest.raises(OSError):
        fsutil.write_private_atomic(target, "{}")
    assert [p.name for p in tmp_path.iterdir()] == []


def test_xdg_home_honors_the_override_and_the_default(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "c"))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    assert fsutil.xdg_home("cache", load_settings()) == tmp_path / "c"
    monkeypatch.setenv("HOME", str(tmp_path))
    assert fsutil.xdg_home("config", load_settings()) == tmp_path / ".config"


def test_private_directory_creation_does_not_need_chmod(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def refuse(path: object, mode: int) -> None:
        raise OSError("no mode bits on this filesystem")

    monkeypatch.setattr("jev_judge_mcp.fsutil.os.chmod", refuse)
    assert fsutil.private_dir(tmp_path / "d") == tmp_path / "d"  # the directory still exists


def test_scout_010_preserves_existing_directory_mode(tmp_path: Path) -> None:
    tmp_path.chmod(0o755)
    with pytest.raises(PermissionError) as refused:
        fsutil.write_private_atomic(tmp_path / "private", "sample")
    assert str(tmp_path) not in str(refused.value)
    assert _mode(tmp_path) == 0o755
    assert not (tmp_path / "private").exists()


@pytest.mark.parametrize("fails", [False, True], ids=["durable", "directory-sync-fails"])
def test_private_write_syncs_the_parent_after_publishing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fails: bool
) -> None:
    target = tmp_path / "record.json"
    target.write_text("previous")
    synced: list[int] = []
    fsync = os.fsync

    def observe(fd: int) -> None:
        info = os.fstat(fd)
        if stat.S_ISDIR(info.st_mode):
            assert target.read_text() == "next"
            synced.append(info.st_ino)
            if fails:
                raise OSError("directory sync failed")
        fsync(fd)

    monkeypatch.setattr(os, "fsync", observe)
    if fails:
        with pytest.raises(OSError, match="directory sync failed"):
            fsutil.write_private_atomic(target, "next")
    else:
        fsutil.write_private_atomic(target, "next")
    assert synced == [tmp_path.stat().st_ino]
    assert list(tmp_path.iterdir()) == [target]
