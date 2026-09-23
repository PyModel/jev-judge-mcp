"""Private files under the user's home: one owner for 0600-in-0700 writes.

`keyfile` (the stored API key) and `cache` (replayed judgments over the caller's State) both write
into the user's home, and both hold material no other local account should read. The mode pair
lives here once — the XDG base, the private leaf directory, and the atomic write that always lands
a fresh 0600 file. A filesystem that cannot express a mode keeps the file (ADR-0032 is POSIX-only);
the write still succeeds.
"""

import os
import tempfile
from pathlib import Path
from typing import Literal

FILE_MODE = 0o600
DIR_MODE = 0o700


def restrict(path: Path, mode: int) -> None:
    """Tighten `mode`; a filesystem that cannot express it keeps the file anyway."""
    try:
        os.chmod(path, mode)
    except OSError:
        pass


def xdg_home(kind: Literal["cache", "config"]) -> Path:
    """The XDG base for `kind`: `$XDG_CACHE_HOME` / `$XDG_CONFIG_HOME`, else the default under home."""
    override = os.environ.get(f"XDG_{kind.upper()}_HOME")
    if override:
        return Path(override)
    return Path.home() / (".cache" if kind == "cache" else ".config")


def private_dir(path: Path) -> Path:
    """`path` exists and is mode 0700. Intermediates keep the user's own modes."""
    path.mkdir(parents=True, exist_ok=True)
    restrict(path, DIR_MODE)
    return path


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
        # mkstemp already created 0600; this re-asserts it on filesystems that need it.
        restrict(Path(temporary), FILE_MODE)
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise
    restrict(path, FILE_MODE)
    return path
