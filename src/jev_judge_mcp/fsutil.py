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
from contextlib import suppress
from pathlib import Path
from typing import Literal

from jev_judge_mcp.settings import Settings

DIR_MODE = 0o700


def read_bounded(path: Path, *, max_bytes: int, follow_symlinks: bool = False) -> bytes:
    """Read one regular file, enforcing the byte limit even when it grows after open.

    Raise OSError for an unsuitable file. Nonblocking open lets fstat reject a pipe without
    waiting for a writer. Callers editing an explicitly symlinked config can opt into following it.
    """
    if max_bytes < 0:
        raise ValueError("max_bytes must be nonnegative")
    flags = os.O_NONBLOCK | os.O_NOCTTY | (0 if follow_symlinks else os.O_NOFOLLOW)
    with open(path, "rb", opener=lambda name, mode: os.open(name, mode | flags)) as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode):
            raise OSError(errno.EINVAL, "expected a regular file", str(path))
        if info.st_size <= max_bytes:
            data = handle.read(max_bytes + 1)
            if len(data) <= max_bytes:
                return data
    raise OSError(errno.EFBIG, f"file exceeds {max_bytes} bytes", str(path))


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
