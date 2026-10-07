"""A throwaway git repository with one real patch, for the paths that read a git range."""

import json
import subprocess
from pathlib import Path

_IDENTITY = ("-c", "user.email=gate@example.invalid", "-c", "user.name=gate test")


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def repo_with_diff(tmp_path: Path) -> Path:
    """Two commits so ``git diff HEAD~1`` is a real patch (`x.py`: `x = 1` to `x = 2`), plus an
    uncommitted `claims.json` and `tests.log` for the gate to read."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True, capture_output=True)
    for value, message in (("1", "init"), ("2", "set x to 2")):
        (repo / "x.py").write_text(f"x = {value}\n", encoding="utf-8")
        _git(repo, "add", "x.py")
        _git(repo, *_IDENTITY, "commit", "-qm", message)
    (repo / "claims.json").write_text(
        json.dumps({"request": "Set x to 2", "claims": ["The patch sets x to 2."]}),
        encoding="utf-8",
    )
    (repo / "tests.log").write_text("1 passed\n", encoding="utf-8")
    return repo


def commit_files(repo: Path, files: dict[str, str], message: str = "more") -> None:
    """Write and commit `files` (relative path to text) on top of ``repo``'s HEAD."""
    for name, text in files.items():
        (repo / name).parent.mkdir(parents=True, exist_ok=True)
        (repo / name).write_text(text, encoding="utf-8")
        _git(repo, "add", name)
    _git(repo, *_IDENTITY, "commit", "-qm", message)


def set_config(repo: Path, key: str, value: str) -> None:
    """One repository-local git setting, as an operator's own config would set it."""
    _git(repo, "config", key, value)
