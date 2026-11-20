"""The build identity on the wire, in the startup log, and from `--version` (ADR-0054).

A wheel reports the installed version. So does a git source tree whose HEAD commit is exactly
the tag `v<version>`. Any other checkout of this distribution reports the PEP 440 local version
`<version>+g<short sha>`. The git state is read from files under `.git` — including a worktree's
`.git` file, `commondir`, loose refs, and `packed-refs` — for the PEP 440 identity.
Git peels annotated tag objects. Session metadata records startup dirtiness and detects source drift.
"""

import logging
import os
import re
import subprocess
import tomllib
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from time import monotonic

import anyio
from anyio.abc import TaskGroup

from jev_judge_mcp import git
from jev_judge_mcp.domain import is_json_object
from jev_judge_mcp.process import OutputTooLarge

logger = logging.getLogger(__name__)

DISTRIBUTION = "jev-judge-mcp"
"""The distribution whose installed version is the plain identity (ADR-0049)."""

SHORT_SHA = 7
"""Hex digits of the local-version suffix: git's classic abbreviation, as in `+gc5fe9a1`."""

_SHA = re.compile(r"^[0-9a-f]{40}$|^[0-9a-f]{64}$")
_MAX_REF_DEPTH = 8
_INSTALLED_DIRS = frozenset({"site-packages", "dist-packages"})
"""A module under one of these is a wheel, even if a parent directory is this checkout."""


def reported_version(start: Path | None = None) -> str:
    """The version this process should report. `start` defaults to this module's file."""
    installed = version(DISTRIBUTION)
    root = _source_root(start if start is not None else Path(__file__))
    if root is None:
        return installed
    dirs = _git_dirs(root)
    if dirs is None:
        return installed
    sha = _resolve_ref(*dirs, "HEAD")
    if sha is None:
        return installed
    tag = _resolve_ref(*dirs, f"refs/tags/v{installed}")
    if tag == sha or (tag is not None and _tag_commit(tag, dirs[0]) == sha):
        return installed
    return f"{installed}+g{sha[:SHORT_SHA]}"


def _source_root(start: Path) -> Path | None:
    """The checkout that contains `start`, or None for a wheel or any other tree.

    The first `pyproject.toml` wins. A foreign manifest stops the walk, so a wheel sitting inside
    some other project cannot pick up a higher checkout. `site-packages` stops it first: an
    installed copy whose venv lives inside this repo is still a wheel.
    """
    origin = start.resolve()
    candidates = (origin, *origin.parents) if origin.is_dir() else origin.parents
    for parent in candidates:
        if parent.name in _INSTALLED_DIRS:
            return None
        manifest = parent / "pyproject.toml"
        if not manifest.is_file():
            continue
        try:
            loaded = tomllib.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, tomllib.TOMLDecodeError):
            return None
        project = loaded.get("project")
        name = project.get("name") if is_json_object(project) else None
        if name == DISTRIBUTION:
            return parent
        return None
    return None


def _git_dirs(root: Path) -> tuple[Path, Path] | None:
    """`(git dir, common dir)` for `root`'s `.git`, or None when it is not a git tree.

    A worktree's `.git` is a file, `gitdir: <path>`, and that directory's `commondir` points at
    the shared refs. A normal checkout's `.git` is the directory, and it is its own common dir.
    """
    marker = root / ".git"
    try:
        if marker.is_dir():
            return marker, marker
        if not marker.is_file():
            return None
        line = next((item.strip() for item in marker.read_text(encoding="utf-8").splitlines() if item.strip()), "")
    except (OSError, UnicodeError):
        return None
    if not line.startswith("gitdir:"):
        return None
    raw = line.split(":", 1)[1].strip()
    if not raw:
        return None
    git_dir = Path(raw)
    if not git_dir.is_absolute():
        git_dir = marker.parent / git_dir
    try:
        git_dir = git_dir.resolve()
    except (OSError, UnicodeError):
        return None
    common = git_dir
    common_file = git_dir / "commondir"
    try:
        if common_file.is_file():
            rel = common_file.read_text(encoding="utf-8").strip()
            if rel:
                common = (git_dir / rel).resolve()
    except (OSError, UnicodeError):
        return git_dir, git_dir
    return git_dir, common


def _bases(git_dir: Path, common: Path) -> tuple[Path, ...]:
    if git_dir == common:
        return (git_dir,)
    return (git_dir, common)


def _ref_name_ok(name: str) -> bool:
    if not name or name.startswith(("/", "\\")):
        return False
    parts = name.split("/")
    return ".." not in parts and "." not in parts


def _resolve_ref(git_dir: Path, common: Path, name: str, depth: int = 0) -> str | None:
    """The commit or tag object a ref names. A loose ref wins over `packed-refs`, as git's does."""
    if depth > _MAX_REF_DEPTH or not _ref_name_ok(name):
        return None
    for base in _bases(git_dir, common):
        path = base / name
        try:
            if not path.is_file():
                continue
            text = path.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeError):
            return None
        if text.startswith("ref:"):
            return _resolve_ref(git_dir, common, text.split(":", 1)[1].strip(), depth + 1)
        lowered = text.lower()
        return lowered if _SHA.fullmatch(lowered) else None
    for base in _bases(git_dir, common):
        found = _packed_ref(base, name)
        if found is not None:
            return found
    return None


def _packed_ref(base: Path, name: str) -> str | None:
    """The sha `packed-refs` stores for `name`, peeled when the next line is `^<commit>`."""
    path = base / "packed-refs"
    try:
        if not path.is_file():
            return None
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        return None
    for index, line in enumerate(lines):
        if not line or line.startswith("#") or line.startswith("^"):
            continue
        parts = line.split()
        if len(parts) != 2 or parts[1] != name:
            continue
        sha = parts[0].lower()
        if not _SHA.fullmatch(sha):
            return None
        if index + 1 < len(lines) and lines[index + 1].startswith("^"):
            peeled = lines[index + 1][1:].strip().lower()
            if _SHA.fullmatch(peeled):
                return peeled
        return sha
    return None


def _tag_commit(tag: str, git_dir: Path) -> str | None:
    """Git peels loose or packed tag objects, including nested annotated tags."""
    try:
        result = git.run(git_dir, "--git-dir", str(git_dir), "rev-parse", "--verify", f"{tag}^{{commit}}")
    except (OSError, subprocess.TimeoutExpired, OutputTooLarge):
        return None
    return result.stdout.strip().decode("ascii", "replace") if result.returncode == 0 else None


@dataclass(frozen=True, slots=True)
class DiskVersion:
    package: str | None
    commit: str | None = None
    source: tuple[tuple[str, int, int, int], ...] | None = None


def _source_stamp(root: Path) -> tuple[tuple[str, int, int, int], ...]:
    """Stat package files without following directory links or reading their contents."""
    manifest = root / "pyproject.toml"
    st = manifest.stat()
    stamps = [(str(manifest), st.st_ino, st.st_size, st.st_ctime_ns)]
    directories = [str(root / "src" / "jev_judge_mcp")]
    remaining = 1024
    while directories:
        with os.scandir(directories.pop()) as entries:
            for entry in entries:
                remaining -= 1
                if remaining < 0:
                    raise OSError("package tree exceeds the bounded identity scan")
                if entry.name == "__pycache__" or entry.name.endswith((".pyc", ".pyo")):
                    continue
                if entry.is_dir(follow_symlinks=False):
                    directories.append(entry.path)
                else:
                    st = entry.stat()
                    stamps.append((entry.path, st.st_ino, st.st_size, st.st_ctime_ns))
    return tuple(sorted(stamps))


def _dirty(root: Path) -> bool | None:
    try:
        status = git.run(root, "status", "--porcelain", "--untracked-files=normal", "--ignore-submodules=none")
    except (OSError, subprocess.TimeoutExpired, OutputTooLarge):
        return None
    return bool(status.stdout) if status.returncode == 0 else None


class ServerVersion:
    """Loaded identity and bounded observations of the current installation."""

    def __init__(self) -> None:
        self.root = _source_root(Path(__file__))
        self.dirs = _git_dirs(self.root) if self.root else None
        self.loaded = self._observe()
        self.dirty = _dirty(self.root) if self.root and self.dirs else None
        self._current = self.loaded
        self._refreshing = False
        self._retry_at = monotonic() + 1 if self.dirs and self.loaded.source is None else 0.0

    def _observe(self) -> DiskVersion:
        if self.root and self.dirs:
            commit = _resolve_ref(*self.dirs, "HEAD")
            try:
                return DiskVersion(None, commit, _source_stamp(self.root))
            except OSError:
                return DiskVersion(None, commit)
        try:
            return DiskVersion(version(DISTRIBUTION))
        except (PackageNotFoundError, OSError, UnicodeError):
            return DiskVersion(None)

    def refresh(self, tasks: TaskGroup) -> None:
        if self._refreshing or monotonic() < self._retry_at:
            return
        self._refreshing = True
        tasks.start_soon(self._refresh)

    async def _refresh(self) -> None:
        try:
            self._current = await anyio.to_thread.run_sync(self._observe)
        except Exception:
            # The refresh runs in the server's task group, so an unexpected error reports the
            # installation unverifiable instead of ending every session.
            logger.warning("installation observation failed", exc_info=True)
            self._current = DiskVersion(None)
        finally:
            self._refreshing = False
        if self.dirs and self._current.source is None:
            self._retry_at = monotonic() + 1

    def field(self) -> dict[str, object]:
        current = self._current
        loaded = self.loaded
        unavailable = (current.commit is None or current.source is None) if self.dirs else current.package is None
        stale = loaded != current or unavailable
        info: dict[str, object] = {
            "loaded": {"package": loaded.package, "commit": loaded.commit, "dirty": self.dirty},
            "current_commit": current.commit,
            "current_package": current.package,
            "restart_needed": stale,
        }
        if stale:
            info["notice"] = (
                "Jev MCP cannot verify the current installation. Check it and restart the server."
                if unavailable
                else "Jev MCP source changed since startup. Restart the server to load it."
            )
        return info
