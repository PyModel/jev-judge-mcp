"""Server-side file reading for the file-judgment tools (ADR-0077).

The server reads the caller-named file as state, so the file's bytes never enter the agent's
context or the payload. Everything here is deterministic and precedes any provider call: a refusal
is a typed verdict about the input, never a judgment. `jev_files_judge` reuses this module for the
per-file read, so the rules stay in one place: the path scope is the server's working directory
with no caller override, known secret stores refuse `secret_file` before any read, the binary
sniff is a NUL scan of the first `FILE_JUDGE.binary_sniff_bytes` bytes, and the size cap is
`FILE_JUDGE.file_units_max` measured in UTF-16 units (ADR-0005). What does get read is redacted
with the ADR-0076 credential-literal detector before it becomes state, so a judgment about a
config file never ships its secrets to the provider.
"""

from pathlib import Path
from typing import NoReturn

from jev_judge_mcp.credential_literal import redact_credential_literals
from jev_judge_mcp.limits import FILE_JUDGE
from jev_judge_mcp.text import length
from jev_judge_mcp.tools.base import ToolError

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

_SECRET_EXACT = frozenset({".env", ".npmrc", ".pypirc", ".netrc", "id_rsa", "id_ed25519", "id_ecdsa"})
_SECRET_EXTENSIONS = (".pem", ".key", ".p12", ".pfx")
_SECRET_ENV_STANDINS = frozenset({".env.example", ".env.sample", ".env.template"})
"""Known secret stores, matched on the resolved file's name (case-folded). The `.env.*` family is
refused except the checked-in stand-ins, so a real environment file is never read but its redacted
example is."""


def is_secret_store(path: Path) -> bool:
    """Whether the file's name is a known secret store: such files are never read.

    Matched on the resolved path's name, so a symlink's target name decides; the check is a pure
    name test and precedes any I/O.
    """
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

    Order is cheapest first: the secret-store name test (pure, no I/O), existence and type
    (`stat`), the byte early-out, then the read with the NUL sniff, then the exact UTF-16
    measurement, and finally the credential-literal redaction. The caller resolves the path
    through `resolve_scoped` first, so scope precedes everything by construction, and every
    refusal here precedes any provider call.
    """
    if is_secret_store(path):
        refuse("secret_file", f"{path.name} is a known secret store and is never read")
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
    # Redaction is the last step, so the cap measures the file as it is on disk, and a clean file
    # passes through unchanged (the detector is the identity without a literal).
    return redact_credential_literals(content)
