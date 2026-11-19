"""Private files under the user's home: one owner for 0600-in-0700 writes.

`keyfile` (the stored API key) and `cache` (replayed judgments over the caller's State) both write
into the user's home, and both hold material no other local account should read. The mode pair
lives here once — the XDG base, the private leaf directory, and the atomic write that always lands
a fresh 0600 file. A directory whose ownership or permissions cannot be verified is refused.
"""

import os
import stat
import tempfile
from pathlib import Path
from typing import Literal

DIR_MODE = 0o700


def xdg_home(kind: Literal["cache", "config"]) -> Path:
    """The XDG base for `kind`: `$XDG_CACHE_HOME` / `$XDG_CONFIG_HOME`, else the default under home."""
    override = os.environ.get(f"XDG_{kind.upper()}_HOME")
    if override:
        return Path(override)
    return Path.home() / (".cache" if kind == "cache" else ".config")


def private_dir(path: Path) -> Path:
    """`path` exists and is mode 0700. Intermediates keep the user's own modes."""
    path.mkdir(mode=DIR_MODE, parents=True, exist_ok=True)
    require_private_dir(path)
    return path


def require_private_dir(path: Path) -> None:
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
        raise PermissionError("choose a private directory owned by the current user with mode 0700")


def write_private_atomic(path: Path, text: str) -> Path:
    """Write `text` to `path` atomically: a fresh 0600 file in a 0700 directory, all or nothing.

    A crash mid-write leaves at most the temporary file; a reader never sees a partial record.
    """
    private_dir(path.parent)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise
    return path
