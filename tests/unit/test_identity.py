"""Build identity: plain version from a wheel or the release tag, local suffix otherwise (ADR-0054)."""

import re
import subprocess
import sys
import zlib
from importlib.metadata import version
from pathlib import Path

from jev_judge_mcp.identity import DISTRIBUTION, reported_version

INSTALLED = version(DISTRIBUTION)
HEAD = "a" * 40
OTHER = "b" * 40
TAG_OBJECT = "c" * 40
PEELED = "d" * 40


def _manifest(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "pyproject.toml").write_text(f'[project]\nname = "{DISTRIBUTION}"\nversion = "0.1.1"\n', encoding="utf-8")


def _start(root: Path) -> Path:
    path = root / "src" / "jev_judge_mcp" / "identity.py"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("", encoding="utf-8")
    return path


def _git(root: Path, head: str, tag: str | None) -> Path:
    """A normal `.git` directory. `tag` is the commit `v<version>` names, when set."""
    _manifest(root)
    git = root / ".git"
    git.mkdir()
    (git / "HEAD").write_text(head + "\n", encoding="utf-8")
    if tag is not None:
        ref = git / "refs" / "tags" / f"v{INSTALLED}"
        ref.parent.mkdir(parents=True)
        ref.write_text(tag + "\n", encoding="utf-8")
    return git


def test_a_tree_that_is_not_this_distribution_reports_the_plain_version(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "other"\n', encoding="utf-8")
    git = tmp_path / ".git"
    git.mkdir()
    (git / "HEAD").write_text(OTHER + "\n", encoding="utf-8")
    assert reported_version(_start(tmp_path)) == INSTALLED


def test_a_wheel_path_inside_the_checkout_reports_the_plain_version(tmp_path: Path) -> None:
    git = _git(tmp_path, OTHER, HEAD)
    assert git.is_dir()
    start = tmp_path / ".venv" / "lib" / "site-packages" / "jev_judge_mcp" / "identity.py"
    start.parent.mkdir(parents=True)
    start.write_text("", encoding="utf-8")
    assert reported_version(start) == INSTALLED


def test_no_git_directory_reports_the_plain_version(tmp_path: Path) -> None:
    _manifest(tmp_path)
    assert reported_version(_start(tmp_path)) == INSTALLED


def test_head_on_the_release_tag_reports_the_plain_version(tmp_path: Path) -> None:
    _git(tmp_path, HEAD, HEAD)
    assert reported_version(_start(tmp_path)) == INSTALLED


def test_head_off_the_release_tag_gains_the_local_suffix(tmp_path: Path) -> None:
    _git(tmp_path, OTHER, HEAD)
    assert reported_version(_start(tmp_path)) == f"{INSTALLED}+g{OTHER[:7]}"


def test_a_missing_release_tag_is_not_that_tag(tmp_path: Path) -> None:
    _git(tmp_path, HEAD, None)
    assert reported_version(_start(tmp_path)) == f"{INSTALLED}+g{HEAD[:7]}"


def test_a_branch_ref_and_packed_refs_tag(tmp_path: Path) -> None:
    git = _git(tmp_path, "ref: refs/heads/main", None)
    (git / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    ref = git / "refs" / "heads" / "main"
    ref.parent.mkdir(parents=True)
    ref.write_text(OTHER + "\n", encoding="utf-8")
    (git / "packed-refs").write_text(
        f"# pack-refs with: peeled fully-peeled sorted\n{HEAD} refs/tags/v{INSTALLED}\n",
        encoding="utf-8",
    )
    assert reported_version(_start(tmp_path)) == f"{INSTALLED}+g{OTHER[:7]}"


def test_a_loose_ref_wins_over_a_stale_packed_ref(tmp_path: Path) -> None:
    git = _git(tmp_path, "ref: refs/heads/main", None)
    (git / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    ref = git / "refs" / "heads" / "main"
    ref.parent.mkdir(parents=True)
    ref.write_text(OTHER + "\n", encoding="utf-8")
    (git / "packed-refs").write_text(
        f"{HEAD} refs/heads/main\n{HEAD} refs/tags/v{INSTALLED}\n",
        encoding="utf-8",
    )
    assert reported_version(_start(tmp_path)) == f"{INSTALLED}+g{OTHER[:7]}"


def test_a_peeled_packed_tag_matches_the_commit_not_the_tag_object(tmp_path: Path) -> None:
    git = _git(tmp_path, PEELED, None)
    (git / "packed-refs").write_text(
        f"{TAG_OBJECT} refs/tags/v{INSTALLED}\n^{PEELED}\n",
        encoding="utf-8",
    )
    assert reported_version(_start(tmp_path)) == INSTALLED


def test_a_loose_annotated_tag_is_peeled_to_its_commit(tmp_path: Path) -> None:
    git = _git(tmp_path, PEELED, TAG_OBJECT)
    body = f"object {PEELED}\ntype commit\ntag v{INSTALLED}\n\nrelease\n".encode()
    raw = f"tag {len(body)}\0".encode() + body
    obj = git / "objects" / TAG_OBJECT[:2] / TAG_OBJECT[2:]
    obj.parent.mkdir(parents=True)
    obj.write_bytes(zlib.compress(raw))
    assert reported_version(_start(tmp_path)) == INSTALLED


def test_a_worktree_git_file_reads_the_common_dir(tmp_path: Path) -> None:
    _manifest(tmp_path)
    common = tmp_path / "common"
    work = common / "worktrees" / "wt"
    work.mkdir(parents=True)
    (work / "HEAD").write_text("ref: refs/heads/topic\n", encoding="utf-8")
    (work / "commondir").write_text("../..\n", encoding="utf-8")
    (work / "gitdir").write_text(str(tmp_path / ".git") + "\n", encoding="utf-8")
    ref = common / "refs" / "heads" / "topic"
    ref.parent.mkdir(parents=True)
    ref.write_text(OTHER + "\n", encoding="utf-8")
    (common / "packed-refs").write_text(f"{HEAD} refs/tags/v{INSTALLED}\n", encoding="utf-8")
    (tmp_path / ".git").write_text(f"gitdir: {work}\n", encoding="utf-8")
    assert reported_version(_start(tmp_path)) == f"{INSTALLED}+g{OTHER[:7]}"


def test_an_unreadable_head_stays_the_plain_version(tmp_path: Path) -> None:
    git = _git(tmp_path, OTHER, HEAD)
    (git / "HEAD").unlink()
    (git / "HEAD").mkdir()
    assert reported_version(_start(tmp_path)) == INSTALLED


def test_this_checkout_reports_a_pep440_identity() -> None:
    identity = reported_version()
    suffix = identity.removeprefix(f"{INSTALLED}+g")
    assert identity == INSTALLED or (identity.startswith(f"{INSTALLED}+g") and re.fullmatch(r"[0-9a-f]{7}", suffix))


def test_version_flag_prints_the_same_identity() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "jev_judge_mcp", "--version"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == f"jev-judge-mcp {reported_version()}\n"
    assert result.stderr == ""
