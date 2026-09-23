"""Private state files and atomic config writes. New installer files are mode 0600."""

import hashlib
import json
import os
import stat
import tempfile
from collections.abc import Mapping
from pathlib import Path

from jev_judge_mcp.domain import is_json_object
from jev_judge_mcp.install.errors import ConfigChangedError
from jev_judge_mcp.install.redact import canonical_hash_payload


def entry_hash(entry: Mapping[str, object]) -> str:
    return hashlib.sha256(canonical_hash_payload(entry).encode()).hexdigest()


def ensure_private_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
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
    real = path.resolve() if path.is_symlink() else path
    if not real.exists():
        return None
    return real.read_bytes()


def atomic_write(path: Path, text: str, mode: int, expected: bytes | None) -> None:
    """Write `text` through a symlink onto the real file. Abort if the file changed."""
    real = path.resolve() if path.is_symlink() else path
    real.parent.mkdir(parents=True, exist_ok=True)
    current = real.read_bytes() if real.exists() else None
    if current != expected:
        raise ConfigChangedError(f"{path}: changed during install, re-run")
    fd, temporary = tempfile.mkstemp(dir=real.parent, prefix=".jev-install-")
    try:
        os.write(fd, text.encode())
        os.fsync(fd)
        os.fchmod(fd, mode)
    finally:
        os.close(fd)
    try:
        current = real.read_bytes() if real.exists() else None
        if current != expected:
            raise ConfigChangedError(f"{path}: changed during install, re-run")
        os.replace(temporary, real)
    except Exception:
        if os.path.exists(temporary):
            os.unlink(temporary)
        raise
    os.chmod(real, mode)


def write_backup(state_dir: Path, stamp: str, target: str, suffix: str, data: bytes) -> Path:
    """Copy a config aside before the first change. The copy can hold other tools' secrets."""
    ensure_private_dir(state_dir)
    backups = state_dir / "backups"
    ensure_private_dir(backups)
    directory = backups / stamp
    ensure_private_dir(directory)
    destination = directory / f"{target}{suffix or '.txt'}"
    fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)
    os.chmod(destination, 0o600)
    return destination


def load_state(path: Path) -> dict[str, object]:
    if not path.is_file():
        return {"targets": {}}
    try:
        parsed: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"targets": {}}
    if not is_json_object(parsed) or not is_json_object(parsed.get("targets")):
        return {"targets": {}}
    return parsed


def state_hash(state: Mapping[str, object], target: str) -> str | None:
    targets = state.get("targets")
    if not is_json_object(targets):
        return None
    record = targets.get(target)
    if not is_json_object(record):
        return None
    value = record.get("entry_sha256")
    return value if isinstance(value, str) else None


def write_state(path: Path, state: Mapping[str, object]) -> None:
    ensure_private_dir(path.parent)
    text = json.dumps(state, indent=2, sort_keys=True) + "\n"
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".jev-state-")
    try:
        os.write(fd, text.encode())
        os.fsync(fd)
        os.fchmod(fd, 0o600)
    finally:
        os.close(fd)
    os.replace(temporary, path)
    os.chmod(path, 0o600)
