"""Server-side file reading for the file-judgment tools (ADR-0077).

The server reads the caller-named file as state, so the file's bytes never enter the agent's
context or the payload. Everything here is deterministic and precedes any provider call: a refusal
is a typed verdict about the input, never a judgment. `jev_files_judge` reuses this module for the
per-file read, so the rules stay in one place: the path scope is the server's working directory
with no caller override, known secret stores refuse `secret_file` before any read, the binary
sniff is a NUL scan of the first bytes of the caller's caps block (the `jev_file_judge` window by
default), and the size cap is that block's `file_units_max` measured in UTF-16 units (ADR-0005).
The per-call guard scrubs configured secrets and credential literals before the text becomes State.
"""

import errno
import logging
import os
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import NoReturn

from jev_judge_mcp.fsutil import read_bounded
from jev_judge_mcp.limits import FILE_JUDGE, FileJudgeCaps, FilesJudgeCaps
from jev_judge_mcp.security import SecurityGuard
from jev_judge_mcp.text import length
from jev_judge_mcp.tools.base import ToolError

logger = logging.getLogger(__name__)

REFUSAL_CODES = (
    "not_found",
    "not_a_file",
    "binary_file",
    "secret_file",
    "file_too_large",
    "path_outside_scope",
)
"""The typed file refusals (ADR-0077). Each one is an `isError` result with this code and no
provider call behind it; the message names the path and the reason."""

_SECRET_EXACT = frozenset({".npmrc", ".pypirc", ".netrc", "id_rsa", "id_ed25519", "id_ecdsa"})
_SECRET_EXTENSIONS = (".env", ".pem", ".key", ".p12", ".pfx")
_SECRET_ENV_STANDINS = frozenset({".env.example", ".env.sample", ".env.template"})
"""Known secret stores, matched on the resolved file's name (case-folded): every `*.env` dotenv
(`secrets.env`, `prod.env`, ...), the dot-prefixed `.env` itself, and the `.env.*` family, except
the checked-in stand-ins, so a real environment file is never read but its redacted example is."""


def is_secret_store(path: Path, guard: SecurityGuard) -> bool:
    """Whether the file's name is a known secret store: such files are never read.

    Matched on the resolved path's name, so a symlink's target name decides. The configured
    key is also excluded by path and file identity.
    """
    key_path = guard.key_path
    try:
        if path.absolute() == key_path.absolute() or path == key_path.resolve() or path.samefile(key_path):
            return True
    except FileNotFoundError:
        pass
    except OSError as error:
        # A searched name too long to be a file cannot be the key; an error checking the key is a match.
        if error.errno != errno.ENAMETOOLONG or error.filename != os.fspath(path):
            return True
    except RuntimeError:
        return True
    name = path.name.lower()
    if name in _SECRET_EXACT:
        return True
    if name.endswith(_SECRET_EXTENSIONS):
        return True
    return name.startswith(".env.") and name not in _SECRET_ENV_STANDINS


def refuse(code: str, message: str) -> NoReturn:
    """Raise the one refusal shape the file tools own."""
    if code not in REFUSAL_CODES:
        raise ValueError(f"{code!r} is not a file-state refusal")
    logger.debug("file refusal code=%s", code)
    raise ToolError(message, code=code)


def resolve_scoped(path: str, scope: Path | None = None) -> Path:
    """The resolved absolute path, or the `path_outside_scope` refusal.

    The scope is the server process's working directory — the launch directory, which already is
    the operator's scope decision. Symlinks are followed, and both `..` segments and symlinked
    targets that resolve outside the scope are refused. There is no caller override: a caller who
    wants another tree launches the server there.
    """
    root = (scope if scope is not None else Path.cwd()).resolve()
    try:
        resolved = (root / path).resolve()
    except (ValueError, OSError, RuntimeError):
        refuse("path_outside_scope", "file path could not be resolved within the working directory")
    if not resolved.is_relative_to(root):
        refuse("path_outside_scope", f"path resolves outside the server's working directory: {path}")
    return resolved


@contextmanager
def pinned_directory(directory: Path) -> Generator[int]:
    """Open each component without following links, then enumerate or open children by descriptor."""
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    fd = os.open(directory.anchor, flags)
    try:
        for component in directory.parts[1:]:
            child = os.open(component, flags, dir_fd=fd)
            os.close(fd)
            fd = child
        yield fd
    finally:
        os.close(fd)


def read_state(
    path: Path,
    guard: SecurityGuard,
    caps: FileJudgeCaps | FilesJudgeCaps = FILE_JUDGE,
) -> str:
    """Read only the validated descriptor, up to the byte ceiling plus one sentinel byte."""
    if is_secret_store(path, guard):
        refuse("secret_file", f"{path.name} is a known secret store and is never read")

    def inspect(info: os.stat_result) -> None:
        try:
            key_info = guard.key_path.stat()
        except FileNotFoundError:
            return
        if os.path.samestat(key_info, info):
            refuse("secret_file", f"{path.name} is the active key store")

    try:
        with pinned_directory(path.parent) as directory:
            data = read_bounded(Path(path.name), dir_fd=directory, max_bytes=4 * caps.file_units_max, inspect=inspect)
    except FileNotFoundError:
        refuse("not_found", f"file not found: {path}")
    except OSError as error:
        if error.errno == errno.EFBIG:
            refuse("file_too_large", f"file exceeds the {caps.file_units_max:,}-unit state cap: {path}")
        refuse("not_a_file", f"path is not a readable file: {path} ({error})")
    except ValueError:
        refuse("not_a_file", f"path is not a readable file: {path}")
    if b"\x00" in data[: caps.binary_sniff_bytes]:
        refuse("binary_file", f"file looks binary (NUL byte in the first {caps.binary_sniff_bytes} bytes): {path}")
    content = data.decode("utf-8", errors="replace")
    if length(content) > caps.file_units_max:
        refuse("file_too_large", f"file exceeds the {caps.file_units_max:,}-unit state cap: {path}")
    scrubbed = guard.scrub(content)
    if length(scrubbed) > caps.file_units_max:
        refuse("file_too_large", f"redacted file exceeds the {caps.file_units_max:,}-unit state cap: {path}")
    return scrubbed
