"""Private state files and atomic config writes. New installer files are mode 0600."""

import contextlib
import ctypes
import errno
import fcntl
import hashlib
import json
import os
import re
import stat
import sys
from collections.abc import Callable, Generator, Mapping
from pathlib import Path
from typing import TypeGuard

from jev_judge_mcp.domain import is_json_object
from jev_judge_mcp.fsutil import (
    ensure_directory,
    private_dir,
    read_bounded,
    require_private_dir,
    stage_write,
    sync_directory,
    write_private_atomic,
)
from jev_judge_mcp.install.errors import ConfigChangedError, InstallError
from jev_judge_mcp.install.redact import canonical_hash_payload

INSTALL_FILE_BYTES_MAX = 16 * 1024 * 1024
BACKUPS_PER_TARGET = 3


def entry_hash(entry: Mapping[str, object]) -> str:
    return hashlib.sha256(canonical_hash_payload(entry).encode()).hexdigest()


def file_mode(path: Path) -> int | None:
    real = path.resolve() if path.is_symlink() else path
    if not real.exists():
        return None
    return stat.S_IMODE(real.stat().st_mode)


def mode_is_loose(mode: int) -> bool:
    """Group or world bits are set. Owner-only 0600 is the limit this installer aims for."""
    return bool(mode & 0o077)


def read_bytes(path: Path) -> bytes | None:
    try:
        return read_bounded(path, max_bytes=INSTALL_FILE_BYTES_MAX, follow=True)
    except FileNotFoundError:
        return None


def _encoded_file(text: str, path: Path) -> bytes:
    data = text.encode("utf-8")
    if len(data) > INSTALL_FILE_BYTES_MAX:
        raise InstallError(f"{path} would exceed the {INSTALL_FILE_BYTES_MAX}-byte installer file limit")
    return data


def _exchange_files(left: Path, right: Path) -> None:
    """Atomically swap two existing names, keeping the actual displaced inode for validation."""
    libc = ctypes.CDLL(None, use_errno=True)
    paths = os.fsencode(left), os.fsencode(right)
    if sys.platform == "darwin" and hasattr(libc, "renamex_np"):
        result = libc.renamex_np(*paths, 0x00000002)  # RENAME_SWAP
    elif sys.platform.startswith("linux") and hasattr(libc, "renameat2"):
        result = libc.renameat2(-100, paths[0], -100, paths[1], 2)  # AT_FDCWD, RENAME_EXCHANGE
    else:
        raise OSError(errno.ENOTSUP, "atomic file exchange is unavailable")
    if result != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), str(right))


def _displaced_digest(path: Path) -> str | None:
    try:
        return digest(read_bounded(path, max_bytes=INSTALL_FILE_BYTES_MAX))
    except FileNotFoundError:
        return None


def _conflict(path: Path, saved: Path) -> str:
    return f"{path} changed during install. The displaced file is at {saved}. Reconcile both files before retrying."


def atomic_write(
    path: Path, text: str, mode: int, expected: bytes | None, *, journal: Callable[[str], None] | None = None
) -> None:
    """Publish without clobbering a new file or losing a concurrent edit. Journal the staging name before committing."""
    data = _encoded_file(text, path)
    real = path.resolve() if path.is_symlink() else path
    ensure_directory(real.parent, 0o777)
    if read_bytes(real) != expected:
        raise ConfigChangedError(f"{path}: changed during install, re-run")
    staged = stage_write(real.parent, ".jev-install-", data, mode)
    retain = False
    try:
        if journal is not None:
            sync_directory(real.parent)
            journal(staged.name)
        if expected is None:
            try:
                os.link(staged, real)
            except FileExistsError:
                raise ConfigChangedError(f"{path}: changed during install, re-run") from None
        else:
            retain = True
            try:
                _exchange_files(staged, real)
            except OSError as exc:
                retain = False
                if isinstance(exc, FileNotFoundError):
                    raise ConfigChangedError(f"{path}: changed during install, re-run") from exc
                raise
            sync_directory(real.parent)
            try:
                displaced = _displaced_digest(staged)
            except OSError as exc:
                raise ConfigChangedError(_conflict(path, staged)) from exc
            if displaced != digest(expected):
                raise ConfigChangedError(_conflict(path, staged))
            retain = False
    finally:
        if not retain:
            staged.unlink()
    sync_directory(real.parent)


def write_backup(state_dir: Path, stamp: str, target: str, suffix: str, data: bytes) -> Path:
    """Copy a config aside before the first change. The copy can hold other tools' secrets."""
    private_dir(state_dir)
    backups = private_dir(state_dir / "backups")
    directory = private_dir(backups / stamp)
    suffix = suffix or ".txt"
    staged = stage_write(directory, f"{target}-", data, 0o600, suffix=suffix)
    destination = directory / f"{target}{suffix}"
    try:
        os.link(staged, destination)
    except FileExistsError:
        destination = staged
    except BaseException:
        staged.unlink()
        raise
    else:
        staged.unlink()
    sync_directory(directory)
    return destination


def prune_backups(state_dir: Path, target: str) -> None:
    """Keep the newest completed copies of this target across all timestamp folders."""
    root = state_dir / "backups"
    try:
        require_private_dir(root)
    except FileNotFoundError:
        return
    pattern = re.compile(rf"{re.escape(target)}(?:-[a-z0-9_]+)?\.[^.]+")
    copies: list[tuple[int, Path]] = []
    for directory in root.iterdir():
        if directory.is_symlink() or not directory.is_dir():
            continue
        for copy in directory.iterdir():
            if pattern.fullmatch(copy.name):
                info = copy.lstat()
                if stat.S_ISREG(info.st_mode):
                    copies.append((info.st_mtime_ns, copy))
    changed: set[Path] = set()
    for _, copy in sorted(copies, reverse=True)[BACKUPS_PER_TARGET:]:
        copy.unlink()
        changed.add(copy.parent)
    for directory in changed:
        sync_directory(directory)
        if not any(directory.iterdir()):
            directory.rmdir()
            sync_directory(root)


@contextlib.contextmanager
def state_lock(directory: Path) -> Generator[None, None, None]:
    private_dir(directory)
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise InstallError("installer state is busy; re-run after the other installer finishes") from None
        yield
    finally:
        os.close(fd)


def digest(data: bytes | None) -> str | None:
    return None if data is None else hashlib.sha256(data).hexdigest()


def _is_digest(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[a-f0-9]{64}", value) is not None


def _is_record(record: object) -> TypeGuard[dict[str, object]]:
    return (
        is_json_object(record)
        and isinstance(record.get("path"), str)
        and isinstance(record.get("name"), str)
        and _is_digest(record.get("entry_sha256"))
    )


def load_targets(path: Path, *, recover: bool) -> dict[str, dict[str, object]]:
    try:
        from jev_judge_mcp.domain import decode_json

        parsed = decode_json(read_bounded(path, max_bytes=INSTALL_FILE_BYTES_MAX, follow=True).decode("utf-8"))
    except FileNotFoundError:
        if path.is_symlink():
            raise InstallError("installer state is a broken symlink") from None
        return {}
    except (OSError, ValueError, RecursionError) as exc:
        reason = exc.strerror if isinstance(exc, OSError) else "invalid or too deeply nested JSON"
        raise InstallError(
            f"could not read installer state {path}: {reason}; check the state file before retrying"
        ) from None
    if not is_json_object(parsed) or not is_json_object(raw := parsed.get("targets")):
        raise InstallError("invalid installer state")
    targets: dict[str, dict[str, object]] = {}
    for key, record in raw.items():
        if not _is_record(record):
            raise InstallError("invalid installer state record")
        targets[key] = record
    if "pending" not in parsed:
        return targets
    pending = parsed["pending"]
    if not is_json_object(pending):
        raise InstallError("invalid pending installer state")
    key, config = pending.get("key"), pending.get("path")
    before, after, record = pending.get("before"), pending.get("after"), pending.get("record")
    if (
        not isinstance(key, str)
        or not isinstance(config, str)
        or not os.path.isabs(config)
        or "\0" in config
        or not _is_digest(after)
        or "before" not in pending
        or (before is not None and not _is_digest(before))
        or "record" not in pending
        or (record is not None and (not _is_record(record) or record["path"] != config))
        or (record is None and (key not in targets or targets[key]["path"] != config))
    ):
        raise InstallError("invalid pending installer state")
    try:
        current = digest(read_bytes(Path(config)))
    except OSError as exc:
        raise InstallError(f"cannot recover installer state: inspect {config} ({exc.strerror})") from exc
    staging = pending.get("staging")
    saved: Path | None = None
    displaced = None
    if staging is not None:
        if not isinstance(staging, str) or not re.fullmatch(r"\.jev-install-[a-zA-Z0-9_-]+", staging):
            raise InstallError("invalid staging name in pending installer state")
        saved = Path(config).resolve().with_name(staging)
        try:
            displaced = _displaced_digest(saved)
        except OSError:
            displaced = "unreadable"
    unexchanged = before is not None and displaced == after
    if current == after and not unexchanged:
        if before is not None and displaced is not None and displaced != before:
            assert saved is not None
            if recover:
                write_state(path, {"targets": targets})
            raise InstallError(_conflict(Path(config), saved))
        if record is None:
            targets.pop(key, None)
        else:
            targets[key] = record
    elif current != before and displaced != after:
        raise InstallError(f"config changed during installer recovery; inspect {config} and {path}")
    if recover:
        if saved is not None:
            try:
                saved.unlink()
            except FileNotFoundError:
                pass
            else:
                sync_directory(saved.parent)
        write_state(path, {"targets": targets})
    return targets


def ownership_key(targets: Mapping[str, object], target: str, path: Path, name: str) -> str:
    path = path.absolute()
    identity = hashlib.sha256(json.dumps([str(path), name]).encode()).hexdigest()
    alternate = f"{target}:{identity}"
    primary = targets.get(target)
    if alternate in targets or (is_json_object(primary) and (primary["path"], primary["name"]) != (str(path), name)):
        return alternate
    return target


def write_state(path: Path, state: Mapping[str, object]) -> None:
    text = json.dumps(state, indent=2, sort_keys=True) + "\n"
    write_private_atomic(path, _encoded_file(text, path))
