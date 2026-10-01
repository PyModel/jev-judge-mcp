"""Server-side file reading for the file-judgment tools (ADR-0077).

The server reads the caller-named file as state, so the file's bytes never enter the agent's
context or the payload. Everything here is deterministic and precedes any provider call: a refusal
is a typed verdict about the input, never a judgment. `jev_files_judge` reuses this module for the
per-file read, so the rules stay in one place: the path scope is the server's working directory
with no caller override, the binary sniff is a NUL scan of the first `FILE_JUDGE.binary_sniff_bytes`
bytes, and the size cap is `FILE_JUDGE.file_units_max` measured in UTF-16 units (ADR-0005).
"""

from pathlib import Path
from typing import NoReturn

from jev_judge_mcp.limits import FILE_JUDGE
from jev_judge_mcp.text import length
from jev_judge_mcp.tools.base import ToolError

REFUSAL_CODES = ("not_found", "not_a_file", "binary_file", "file_too_large", "path_outside_scope")
"""The typed file refusals (ADR-0077). Each one is an `isError` result with this code and no
provider call behind it; the message names the path and the reason."""


def refuse(code: str, message: str) -> NoReturn:
    """Raise the one refusal shape the file tools own."""
    if code not in REFUSAL_CODES:
        raise ValueError(f"{code!r} is not a file-state refusal")
    raise ToolError(message, code=code)


def resolve_scoped(path: str, scope: Path | None = None) -> Path:
    """The resolved absolute path, or the `path_outside_scope` refusal.

    The scope is the server process's working directory — the launch directory, which already is
    the operator's scope decision. Symlinks are followed, and both `..` segments and symlinked
    targets that resolve outside the scope are refused. There is no caller override: a caller who
    wants another tree launches the server there.
    """
    root = (scope if scope is not None else Path.cwd()).resolve()
    resolved = (root / path).resolve()
    if not resolved.is_relative_to(root):
        refuse("path_outside_scope", f"path resolves outside the server's working directory: {path}")
    return resolved


def read_state(path: Path) -> str:
    """The decoded text of a scoped regular file, or its typed refusal; no content is echoed.

    Order is cheapest first: existence and type (`stat`), the byte early-out, then the read with
    the NUL sniff, then the exact UTF-16 measurement. The caller resolves the path through
    `resolve_scoped` first, so scope precedes everything by construction, and every refusal here
    precedes any provider call.
    """
    try:
        stat = path.stat()
    except FileNotFoundError:
        refuse("not_found", f"file not found: {path}")
    except OSError as error:
        refuse("not_a_file", f"path is not a readable file: {path} ({error.strerror or error})")
    if not path.is_file():
        refuse("not_a_file", f"path is not a regular file: {path}")
    # Every UTF-8 byte sequence — valid, or replaced when undecodable — measures at least one
    # UTF-16 unit per four bytes, so a file over four times the unit cap is over-cap unread.
    if stat.st_size > 4 * FILE_JUDGE.file_units_max:
        refuse("file_too_large", f"file exceeds the {FILE_JUDGE.file_units_max:,}-unit state cap: {path}")
    data = path.read_bytes()
    if b"\x00" in data[: FILE_JUDGE.binary_sniff_bytes]:
        refuse(
            "binary_file", f"file looks binary (NUL byte in the first {FILE_JUDGE.binary_sniff_bytes} bytes): {path}"
        )
    content = data.decode("utf-8", errors="replace")
    if length(content) > FILE_JUDGE.file_units_max:
        refuse("file_too_large", f"file exceeds the {FILE_JUDGE.file_units_max:,}-unit state cap: {path}")
    return content
