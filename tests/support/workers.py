"""Process queries scoped to test-owned PIDs and process groups."""

import os
import re
import subprocess
import sys

WORKER_MODULE = "jev_judge_mcp.extract.worker"


def worker_pids(group: int) -> set[int]:
    """Return only worker PIDs, including workers reparented after their server exited."""
    if group <= 0:
        raise ValueError("a test server's process group must be positive")
    # pgrep emits only PIDs and the kernel keeps the group across reparenting. Match the
    # complete Python module invocation so a command that mentions the module is excluded.
    python = rf"({re.escape(sys.executable)}|([^[:space:]]*/)?[Pp]ython[0-9.]*)"
    pattern = rf"{python} -P -m {re.escape(WORKER_MODULE)}"
    result = subprocess.run(["pgrep", "-g", str(group), "-f", "-x", pattern], capture_output=True, text=True, timeout=5)
    if result.returncode == 1:  # pgrep's documented no-match status
        return set()
    result.check_returncode()
    return {int(pid) for pid in result.stdout.split()}


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def gone_or_zombie(pid: int) -> bool:
    """A child that exited need not have been reaped by its final parent yet."""
    if pid <= 0:
        raise ValueError("a test process PID must be positive")
    if not alive(pid):
        return True
    state = subprocess.run(
        ["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True, timeout=5, check=False
    ).stdout.strip()
    return not state or state.startswith("Z")
