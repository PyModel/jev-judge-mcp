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
import tempfile
from collections.abc import Callable, Generator, Mapping
from pathlib import Path
from typing import TypeGuard

from jev_judge_mcp.domain import is_json_object
from jev_judge_mcp.fsutil import read_bounded
from jev_judge_mcp.install.errors import ConfigChangedError, InstallError
from jev_judge_mcp.install.redact import canonical_hash_payload

INSTALL_FILE_BYTES_MAX = 16 * 1024 * 1024


def entry_hash(entry: Mapping[str, object]) -> str:
    return hashlib.sha256(canonical_hash_payload(entry).encode()).hexdigest()


def _ensure_directory(path: Path, mode: int) -> None:
    if path.is_dir():
        return
    _ensure_directory(path.parent, mode)
    path.mkdir(mode=mode, exist_ok=True)
    _sync_directory(path.parent)


def ensure_private_dir(path: Path) -> None:
    if path.is_symlink():
        raise InstallError(f"installer state directory is a symlink: {path}")
    _ensure_directory(path, 0o700)
    os.chmod(path, 0o700)


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
        return read_bounded(path, max_bytes=INSTALL_FILE_BYTES_MAX, follow_symlinks=True)
    except FileNotFoundError:
        return None


def _encoded_file(text: str, path: Path) -> bytes:
    data = text.encode("utf-8")
    if len(data) > INSTALL_FILE_BYTES_MAX:
        raise InstallError(f"{path} would exceed the {INSTALL_FILE_BYTES_MAX}-byte installer file limit")
    return data


def _write_all(fd: int, data: bytes) -> None:
    """`os.write` may take fewer bytes than offered; loop until every byte is down."""
    view = memoryview(data)
    while view:
        written = os.write(fd, view)
        if not written:
            raise OSError("short write made no progress")
        view = view[written:]


def _write_temporary(directory: Path, prefix: str, data: bytes, mode: int) -> str:
    """A fully written, synced, mode-set temporary file beside the target, or no file at all:
    a failure at any step unlinks it before the error propagates."""
    fd, temporary = tempfile.mkstemp(dir=directory, prefix=prefix)
    try:
        try:
            _write_all(fd, data)
            os.fchmod(fd, mode)
            os.fsync(fd)
        finally:
            os.close(fd)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(temporary)
        raise
    return temporary


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
    _ensure_directory(real.parent, 0o777)
    if read_bytes(real) != expected:
        raise ConfigChangedError(f"{path}: changed during install, re-run")
    staged = Path(_write_temporary(real.parent, ".jev-install-", data, mode))
    retain = False
    try:
        if journal is not None:
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
            _sync_directory(real.parent)
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
    _sync_directory(real.parent)


def write_backup(state_dir: Path, stamp: str, target: str, suffix: str, data: bytes) -> Path:
    """Copy a config aside before the first change. The copy can hold other tools' secrets."""
    ensure_private_dir(state_dir)
    backups = state_dir / "backups"
    ensure_private_dir(backups)
    directory = backups / stamp
    ensure_private_dir(directory)
    destination = directory / f"{target}{suffix or '.txt'}"
    try:
        fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        fd, unique = tempfile.mkstemp(dir=directory, prefix=f"{target}-", suffix=suffix or ".txt")
        destination = Path(unique)
    try:
        try:
            _write_all(fd, data)
            os.fsync(fd)
        finally:
            os.close(fd)
        _sync_directory(directory)
    except BaseException:
        destination.unlink()
        raise
    return destination


def _sync_directory(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


@contextlib.contextmanager
def state_lock(directory: Path) -> Generator[None, None, None]:
    ensure_private_dir(directory)
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
        parsed: object = json.loads(
            read_bounded(path, max_bytes=INSTALL_FILE_BYTES_MAX, follow_symlinks=True).decode("utf-8")
        )
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
    if staging is not None:
        if not isinstance(staging, str) or not re.fullmatch(r"\.jev-install-[a-zA-Z0-9_-]+", staging):
            raise InstallError("invalid staging name in pending installer state")
        if current == after and before is not None:
            saved = Path(config).resolve().with_name(staging)
            try:
                displaced = _displaced_digest(saved)
            except OSError:
                displaced = "unreadable"
            if displaced is not None and displaced != before:
                if recover:
                    write_state(path, {"targets": targets})
                raise InstallError(_conflict(Path(config), saved))
    if current == after:
        if record is None:
            targets.pop(key, None)
        else:
            targets[key] = record
    elif current != before:
        raise InstallError(f"config changed during installer recovery; inspect {config} and {path}")
    if recover:
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
    ensure_private_dir(path.parent)
    text = json.dumps(state, indent=2, sort_keys=True) + "\n"
    temporary = _write_temporary(path.parent, ".jev-state-", _encoded_file(text, path), 0o600)
    try:
        os.replace(temporary, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(temporary)
        raise
    _sync_directory(path.parent)
