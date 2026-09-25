"""No test may census extract workers machine-wide without a parent pid.

The taken-port test used to collect every `jev_judge_mcp.extract.worker` on the host. Another
server recycling its pool made that set change, and the equality check failed. The parent-scoped
census lives in `tests/support/workers.py`. A test that runs `ps -ax` or `ps -A` and names the
worker module, without asking `ps` for `ppid`, is the same failure class.
"""

import ast
import subprocess
from collections.abc import Sequence
from pathlib import Path

import pytest

from tests.support.workers import WORKER_MODULE

REPO = Path(__file__).resolve().parents[2]


def owned_test_py_files(root: Path = REPO) -> list[Path]:
    """Repo-owned test modules, including untracked ones and excluding gitignored trees.

    An empty listing is an error: scanning nothing would pass vacuously."""
    listing = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard", "--", "tests"],
        capture_output=True,
        text=True,
        cwd=root,
        check=False,
    )
    if listing.returncode != 0:
        raise RuntimeError(f"git could not list test files: {listing.stderr.strip()}")
    files = [root / line for line in listing.stdout.split("\0") if line.endswith(".py") and (root / line).is_file()]
    if not files:
        raise RuntimeError("git listed no test python files; refusing to scan nothing")
    return files


def _ps_args(node: ast.AST) -> list[str] | None:
    if not isinstance(node, ast.Call) or not node.args:
        return None
    first = node.args[0]
    if not isinstance(first, ast.List):
        return None
    values: list[str] = []
    for elt in first.elts:
        if not isinstance(elt, ast.Constant) or not isinstance(elt.value, str):
            return None
        values.append(elt.value)
    if not values or values[0] != "ps":
        return None
    return values


def unscoped_worker_census(source: str, module: str) -> bool:
    """True when `source` runs a machine-wide `ps` and names `module`, without a parent-pid column."""
    tree = ast.parse(source)
    names_module = any(isinstance(node, ast.Constant) and node.value == module for node in ast.walk(tree))
    if not names_module:
        return False
    for node in ast.walk(tree):
        args = _ps_args(node)
        if args is None:
            continue
        if ("-ax" in args or "-A" in args) and not any("ppid" in arg for arg in args):
            return True
    return False


def machine_wide_worker_censuses(files: Sequence[Path], module: str) -> list[str]:
    if not files:
        raise ValueError("no test files to scan")
    return [str(path) for path in files if unscoped_worker_census(path.read_text(encoding="utf-8"), module)]


def test_no_test_takes_an_unscoped_machine_wide_worker_census() -> None:
    assert machine_wide_worker_censuses(owned_test_py_files(), WORKER_MODULE) == []


def test_the_guard_catches_an_unscoped_census_and_refuses_to_scan_nothing(tmp_path: Path) -> None:
    bad = tmp_path / "test_bad.py"
    bad.write_text(
        "import subprocess\n"
        "def census() -> set[str]:\n"
        '    result = subprocess.run(["ps", "-ax", "-o", "pid=,command="], capture_output=True, text=True)\n'
        "    found: set[str] = set()\n"
        "    for line in result.stdout.splitlines():\n"
        '        pid, _, command = line.strip().partition(" ")\n'
        f'        if "{WORKER_MODULE}" in command:\n'
        "            found.add(pid)\n"
        "    return found\n"
    )
    scoped = tmp_path / "test_scoped.py"
    scoped.write_text(
        "import subprocess\n"
        "def census() -> str:\n"
        '    subprocess.run(["ps", "-A", "-o", "pid=,ppid=,command="], check=True)\n'
        f'    return "{WORKER_MODULE}"\n'
    )
    unrelated = tmp_path / "test_unrelated.py"
    unrelated.write_text(
        "import subprocess\n"
        "def census() -> None:\n"
        '    subprocess.run(["ps", "-ax", "-o", "pid=,command="], check=False)\n'
    )
    assert machine_wide_worker_censuses([bad, scoped, unrelated], WORKER_MODULE) == [str(bad)]
    with pytest.raises(ValueError, match="no test files"):
        machine_wide_worker_censuses([], WORKER_MODULE)
