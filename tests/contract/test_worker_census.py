"""Guard against host-wide command-line disclosure and unscoped extract-worker counts."""

import ast
import re
import subprocess
from collections.abc import Sequence
from pathlib import Path

import pytest

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


def unsafe_process_listing(source: str) -> bool:
    """Test process probes must select their target rather than collect a default host listing."""
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call) or not node.args or not isinstance(node.args[0], (ast.List, ast.Tuple)):
            continue
        argv = node.args[0].elts
        if not argv or not isinstance(argv[0], ast.Constant) or argv[0].value != "ps":
            continue
        flags = {arg.value for arg in argv[1:] if isinstance(arg, ast.Constant) and isinstance(arg.value, str)}
        # BSD and UNIX ps combine short flags (auxww, -ef). Additional selectors
        # may broaden a -p query, so a PID alone is insufficient in a mixed invocation.
        unscoped = any(re.fullmatch(r"-[A-Za-z]*[AaegNUu][A-Za-z]*|a[uxwl]*|--deselect", arg) for arg in flags)
        if unscoped or not flags.intersection({"-p", "--pid", "--ppid", "--pgroup"}):
            return True
    return False


def unsafe_process_listings(files: Sequence[Path]) -> list[str]:
    if not files:
        raise ValueError("no test files to scan")
    return [str(path) for path in files if unsafe_process_listing(path.read_text(encoding="utf-8"))]


def test_process_listings_keep_host_arguments_private_and_worker_counts_scoped() -> None:
    assert unsafe_process_listings(owned_test_py_files()) == []


@pytest.mark.parametrize(
    "argv",
    [
        '["ps", "-ax", "-o", "pid=,command="]',
        '["ps", "-A", "-o", "pid=,ppid=,command="]',
        '["ps", "-A"]',
        '["ps", "-ef"]',
        '["ps", "auxww"]',
        '["ps", "-ax", "-o", fields]',
        '["ps", "-ef", "-p", str(pid)]',
    ],
)
def test_guard_detects_host_command_lines(argv: str) -> None:
    assert unsafe_process_listing(f"subprocess.run({argv})")


def test_guard_accepts_a_pid_scoped_probe_and_refuses_an_empty_scan(tmp_path: Path) -> None:
    scoped = tmp_path / "test_scoped.py"
    scoped.write_text('subprocess.run(["ps", "-p", str(pid), "-o", "stat="])')
    assert unsafe_process_listings([scoped]) == []
    with pytest.raises(ValueError, match="no test files"):
        unsafe_process_listings([])
