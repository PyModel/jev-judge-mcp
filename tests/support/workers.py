"""Parent-scoped census of extract worker processes.

A machine-wide listing of `jev_judge_mcp.extract.worker` changes whenever any other server on the
host recycles its pool. Callers name the server they started; only that process's descendants,
and workers reparented to init, count.
"""

import os
import shlex
import subprocess

WORKER_MODULE = "jev_judge_mcp.extract.worker"


def process_snapshot() -> dict[int, tuple[int, list[str]]]:
    listing = subprocess.run(
        ["ps", "-A", "-ww", "-o", "pid=,ppid=,command="], capture_output=True, text=True, check=True
    )
    processes: dict[int, tuple[int, list[str]]] = {}
    for line in listing.stdout.splitlines():
        fields = line.split(None, 2)
        if len(fields) != 3:
            continue
        try:
            pid, ppid = int(fields[0]), int(fields[1])
            argv = shlex.split(fields[2])
        except ValueError:
            continue
        if argv:
            processes[pid] = (ppid, argv)
    return processes


def is_worker(argv: list[str]) -> bool:
    return any(argv[index : index + 2] == ["-m", WORKER_MODULE] for index in range(len(argv) - 1))


def worker_pids(root_pid: int, processes: dict[int, tuple[int, list[str]]] | None = None) -> set[int]:
    """Workers below root, including through launchers, matched by the exact `-m MODULE` argv pair."""
    table = processes if processes is not None else process_snapshot()
    children: dict[int, list[int]] = {}
    for pid, (ppid, _) in table.items():
        children.setdefault(ppid, []).append(pid)
    descendants: set[int] = set()
    pending = [root_pid]
    while pending:
        parent = pending.pop()
        for child in children.get(parent, []):
            if child not in descendants:
                descendants.add(child)
                pending.append(child)
    return {pid for pid in descendants if pid in table and is_worker(table[pid][1])}


def orphan_worker_pids() -> set[int]:
    return {pid for pid, (ppid, argv) in process_snapshot().items() if ppid == 1 and is_worker(argv)}


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def ps_snapshot() -> str:
    """The whole process listing, for the failure message: what exists when an assert about
    processes fails, on any runner."""
    return subprocess.run(
        ["ps", "-A", "-ww", "-o", "pid=,ppid=,command="], capture_output=True, text=True, check=True
    ).stdout
