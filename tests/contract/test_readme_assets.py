"""README links and images point at files the tree actually tracks.

Deleting banner.svg while the page still named it would have passed every other
check. This fails when a relative image or link target is missing or untracked.
"""

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).parents[2]
_TARGET = re.compile(r"""(?:src|href)=["']([^"']+)["']|\]\(([^)\s]+)""")


def _relative(raw: str) -> str | None:
    path = raw.split("#", 1)[0]
    if not path or path.startswith(("#", "http://", "https://", "mailto:")):
        return None
    return path.removeprefix("./")


def test_readme_relative_targets_are_tracked() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    tracked = set(subprocess.check_output(["git", "ls-files"], cwd=ROOT, text=True).splitlines())
    missing: list[str] = []
    for match in _TARGET.finditer(readme):
        rel = _relative(match.group(1) or match.group(2))
        if rel is None:
            continue
        prefix = rel.rstrip("/") + "/"
        if rel not in tracked and not any(item.startswith(prefix) for item in tracked):
            missing.append(rel)
    assert missing == [], f"README points at paths that are not tracked: {missing}"
