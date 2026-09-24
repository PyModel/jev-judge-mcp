"""The build identity on the wire, in the startup log, and from `--version` (ADR-0054).

A wheel reports the installed version. So does a git source tree whose HEAD commit is exactly
the tag `v<version>`. Any other checkout of this distribution reports the PEP 440 local version
`<version>+g<short sha>`. The git state is read from files under `.git` — including a worktree's
`.git` file, `commondir`, loose refs, and `packed-refs` — and never by spawning git. A dirty
worktree is not detected: the same commit is the same build.
"""

import re
import tomllib
import zlib
from importlib.metadata import version
from pathlib import Path

from jev_judge_mcp.domain import is_json_object

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
    tag = _peel(_resolve_ref(*dirs, f"refs/tags/v{installed}"), *dirs)
    if tag is not None and tag == sha:
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
        except (OSError, tomllib.TOMLDecodeError):
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
    except OSError:
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
    except OSError:
        return None
    common = git_dir
    common_file = git_dir / "commondir"
    try:
        if common_file.is_file():
            rel = common_file.read_text(encoding="utf-8").strip()
            if rel:
                common = (git_dir / rel).resolve()
    except OSError:
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
        except OSError:
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
    except OSError:
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


def _peel(sha: str | None, git_dir: Path, common: Path) -> str | None:
    """Follow a loose annotated tag to its commit. A missing object is left as-is.

    `packed-refs` peels via its `^` line. A loose tag ref names the tag object; without this
    step, HEAD on the tagged commit would not match `v<version>` and a release checkout would
    wear a dev suffix.
    """
    if sha is None:
        return None
    current = sha
    for _ in range(4):
        raw = next((obj for base in _bases(git_dir, common) if (obj := _loose_object(current, base)) is not None), None)
        target = _tag_target(raw) if raw is not None else None
        if target is None:
            return current
        current = target
    return current


def _loose_object(sha: str, base: Path) -> bytes | None:
    path = base / "objects" / sha[:2] / sha[2:]
    try:
        if not path.is_file():
            return None
        return zlib.decompress(path.read_bytes())
    except (OSError, zlib.error):
        return None


def _tag_target(raw: bytes) -> str | None:
    """The `object` line of a tag object, or None when `raw` is a commit or anything else."""
    if not raw.startswith(b"tag "):
        return None
    nul = raw.find(b"\0")
    if nul < 0:
        return None
    for line in raw[nul + 1 :].splitlines():
        if line.startswith(b"object "):
            target = line.removeprefix(b"object ").decode("ascii", "replace").strip().lower()
            return target if _SHA.fullmatch(target) else None
    return None
