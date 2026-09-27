"""Fail if a treehouse worktree build dropped .gitignore and shipped ignored files.

Hatchling treats a gitignore hit on the absolute project root as "exclude nothing".
`/.treehouse/` must not match a checkout that merely lives under `~/.treehouse`.
"""

import sys
import tarfile
from pathlib import Path

import pathspec

ROOT = Path(__file__).resolve().parents[2]
FORBIDDEN = (".venv/", "__pycache__/", ".pytest_cache/", ".ruff_cache/", ".jevbench-tmp/", "docs/reference/jev-skill/")


def main() -> int:
    patterns = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    spec = pathspec.GitIgnoreSpec.from_lines(patterns)
    root = str(ROOT)
    if spec.match_file(root):
        print(f"gitignore excludes the project root {root}; hatchling would ship ignored files", file=sys.stderr)
        return 1
    synthetic = "/var/empty/.treehouse/pool/1/jev-mcp"
    if spec.match_file(synthetic):
        print(f"gitignore excludes a treehouse worktree root ({synthetic})", file=sys.stderr)
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
