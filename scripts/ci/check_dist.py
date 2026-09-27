"""Fail if a treehouse worktree build dropped .gitignore and shipped ignored files.

Hatchling treats a gitignore hit on the absolute project root as "exclude nothing".
An unanchored `.treehouse/` matches a checkout that merely lives under `~/.treehouse`.
The pattern must be anchored at the repository root.

The same check owns two provenance contracts: both artifacts carry
THIRD_PARTY_NOTICES.md (the sdist at the archive root, the wheel in
`.dist-info/licenses/`), and every packaged file matches a `tool.uv` cache-key
glob, or an edit to it serves a stale build. Exactly one artifact per kind may
sit in `dist/`, so a stale build can never be the one inspected.
"""

import fnmatch
import sys
import tarfile
import tomllib
import zipfile
from pathlib import Path
from typing import TypeGuard, cast

ROOT = Path(__file__).resolve().parents[2]
FORBIDDEN = (".venv/", "__pycache__/", ".pytest_cache/", ".ruff_cache/", ".jevbench-tmp/", "docs/reference/jev-skill/")
NOTICE = "THIRD_PARTY_NOTICES.md"


def _one(pattern: str) -> Path:
    found = sorted((ROOT / "dist").glob(pattern))
    if len(found) != 1:
        raise SystemExit(f"expected exactly one {pattern} in dist/, found {len(found)}; rm -rf dist && uv build")
    return found[0]


def _table(value: object) -> TypeGuard[dict[str, object]]:
    return isinstance(value, dict)


def _cache_key_globs(root: Path) -> list[str]:
    parsed: object = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    if not _table(parsed):
        return []
    tool = parsed.get("tool")
    if not _table(tool):
        return []
    uv = tool.get("uv")
    if not _table(uv):
        return []
    keys = uv.get("cache-keys")
    if not isinstance(keys, list):
        return []
    globs: list[str] = []
    for key in cast(list[object], keys):
        if not _table(key):
            continue
        file = key.get("file")
        if isinstance(file, str):
            globs.append(file)
    return globs


def check_wheel_cache_keys(root: Path, wheel: Path) -> list[str]:
    """Every packaged file must match a tool.uv cache-key glob, or an edit to it serves a stale build."""
    globs = _cache_key_globs(root)
    with zipfile.ZipFile(wheel) as archive:
        members = [name for name in archive.namelist() if name.startswith("jev_judge_mcp/")]
    # fnmatch's `*` crosses `/`, so `src/**/*.py` also matches `src/jev_judge_mcp/x.py`.
    return [name for name in members if not any(fnmatch.fnmatch(f"src/{name}", glob) for glob in globs)]


def main() -> int:
    lines = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    if ".treehouse/" in lines:
        print("unanchored .treehouse/ gitignore matches a treehouse worktree root", file=sys.stderr)
        return 1
    if "/.treehouse/" not in lines:
        print("missing anchored /.treehouse/ gitignore entry", file=sys.stderr)
        return 1
    sdist = _one("*.tar.gz")
    with tarfile.open(sdist) as archive:
        names = archive.getnames()
    leaked = [name for name in names if any(part in name for part in FORBIDDEN)]
    if leaked:
        print("sdist contains ignored paths:", file=sys.stderr)
        for name in leaked[:20]:
            print(f"  {name}", file=sys.stderr)
        return 1
    top = sdist.name.removesuffix(".tar.gz")
    if f"{top}/{NOTICE}" not in names:
        print(f"sdist is missing {NOTICE}", file=sys.stderr)
        return 1
    wheel = _one("*.whl")
    dist_info = "-".join(wheel.name.split("-")[:2]) + ".dist-info"
    with zipfile.ZipFile(wheel) as archive:
        wheel_names = archive.namelist()
    if f"{dist_info}/licenses/{NOTICE}" not in wheel_names:
        print(f"wheel licenses/ is missing {NOTICE}", file=sys.stderr)
        return 1
    missed = check_wheel_cache_keys(ROOT, wheel)
    if missed:
        print("wheel files no cache-key glob covers; add the path to [tool.uv] cache-keys:", file=sys.stderr)
        for name in missed:
            print(f"  {name}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
