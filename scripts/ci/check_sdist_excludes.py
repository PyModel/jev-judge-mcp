"""Fail if a treehouse worktree build dropped .gitignore and shipped ignored files.

Hatchling treats a gitignore hit on the absolute project root as "exclude nothing".
An unanchored `.treehouse/` matches a checkout that merely lives under `~/.treehouse`.
The pattern must be anchored at the repository root.
"""

import sys
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FORBIDDEN = (".venv/", "__pycache__/", ".pytest_cache/", ".ruff_cache/", ".jevbench-tmp/", "docs/reference/jev-skill/")


def main() -> int:
    lines = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    if ".treehouse/" in lines:
        print("unanchored .treehouse/ gitignore matches a treehouse worktree root", file=sys.stderr)
        return 1
    if "/.treehouse/" not in lines:
        print("missing anchored /.treehouse/ gitignore entry", file=sys.stderr)
        return 1
    sdists = sorted((ROOT / "dist").glob("*.tar.gz"))
    if not sdists:
        print("no sdist in dist/; run uv build first", file=sys.stderr)
        return 1
    with tarfile.open(sdists[-1]) as archive:
        names = archive.getnames()
    leaked = [name for name in names if any(part in name for part in FORBIDDEN)]
    if leaked:
        print("sdist contains ignored paths:", file=sys.stderr)
        for name in leaked[:20]:
            print(f"  {name}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
