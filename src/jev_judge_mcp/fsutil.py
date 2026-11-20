"""Private files under the user's home: one owner for 0600-in-0700 writes.

The stored API key, cached judgments, and installer state live under the user's home
and can hold material no other local account should read. The mode pair
lives here once — the XDG base, the private leaf directory, and the atomic write that always lands
a fresh 0600 file. A directory whose ownership or permissions cannot be verified is refused.
"""

import errno
import os
import stat
import tempfile
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from typing import Literal

from jev_judge_mcp.settings import Settings

DIR_MODE = 0o700


class NotRegularFile(OSError):
    """A descriptor that cannot be read as file data."""


def read_bounded(
    path: Path,
    *,
    max_bytes: int,
    dir_fd: int | None = None,
    follow: bool = False,
    tail: bool = False,
    inspect: Callable[[os.stat_result], None] | None = None,
) -> bytes:
    """Read a bounded regular-file snapshot after checking its opened descriptor."""
    if max_bytes < 0:
        raise ValueError("max_bytes must be nonnegative")
    flags = os.O_RDONLY | os.O_NONBLOCK | os.O_NOCTTY | (0 if follow else os.O_NOFOLLOW)
    fd = os.open(path, flags, dir_fd=dir_fd)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise NotRegularFile(errno.EINVAL, "expected a regular file", str(path))
        if inspect is not None:
            inspect(info)
        if not tail and info.st_size > max_bytes:
            raise OSError(errno.EFBIG, f"file exceeds {max_bytes} bytes", str(path))
        with os.fdopen(fd, "rb", closefd=False) as handle:
            if tail:
                handle.seek(max(0, info.st_size - max_bytes))
                return handle.read(max_bytes)
            data = handle.read(max_bytes + 1)
        if len(data) > max_bytes:
            raise OSError(errno.EFBIG, f"file exceeds {max_bytes} bytes", str(path))
        return data
    finally:
        os.close(fd)


def xdg_home(kind: Literal["cache", "config"], settings: Settings) -> Path:
    """The XDG base for `kind`: `$XDG_CACHE_HOME` / `$XDG_CONFIG_HOME`, else the default under home."""
    override = settings.xdg_cache_home if kind == "cache" else settings.xdg_config_home
    return override if override is not None else Path.home() / f".{kind}"


def private_dir(path: Path) -> Path:
    """`path` exists and is mode 0700. Intermediates keep the user's own modes."""
    if not path.is_dir():
        ensure_directory(path.parent, 0o777)
        path.mkdir(mode=DIR_MODE, exist_ok=True)
        sync_directory(path.parent)
    require_private_dir(path)
    return path


def require_private_dir(path: Path) -> None:
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
        raise PermissionError(errno.EACCES, "choose a private directory owned by the current user with mode 0700")


def ensure_directory(path: Path, mode: int) -> None:
    if path.is_dir():
        return
    ensure_directory(path.parent, mode)
    path.mkdir(mode=mode, exist_ok=True)
    sync_directory(path.parent)


def stage_write(directory: Path, prefix: str, data: bytes, mode: int, *, suffix: str = "") -> Path:
    """Write and sync a temporary file beside its destination, removing it on failure."""
    fd, temporary = tempfile.mkstemp(dir=directory, prefix=prefix, suffix=suffix)
    try:
        try:
            view = memoryview(data)
            while view:
                written = os.write(fd, view)
                if not written:
                    raise OSError("short write made no progress")
                view = view[written:]
            os.fchmod(fd, mode)
            os.fsync(fd)
        finally:
            os.close(fd)
    except BaseException:
        with suppress(OSError):
            os.unlink(temporary)
        raise
    return Path(temporary)


def sync_directory(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def write_private_atomic(path: Path, text: str | bytes) -> Path:
    """Publish a synced 0600 file in a private directory, then sync the publication."""
    private_dir(path.parent)
    data = text.encode() if isinstance(text, str) else text
    temporary = stage_write(path.parent, f".{path.name}.", data, 0o600)
    try:
        os.replace(temporary, path)
    except BaseException:
        with suppress(OSError):
            temporary.unlink()
        raise
    sync_directory(path.parent)
    return path
