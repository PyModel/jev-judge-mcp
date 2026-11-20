"""Capture subprocess output under one deadline and a per-stream byte bound."""

import contextlib
import os
import signal
import subprocess
import threading
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import BinaryIO


class OutputTooLarge(Exception):
    """A subprocess exceeded its capture bound."""


def capture(
    argv: Sequence[str], *, cwd: Path, env: Mapping[str, str], timeout_seconds: int, byte_cap: int
) -> subprocess.CompletedProcess[bytes]:
    collected: dict[str, bytes] = {"stdout": b"", "stderr": b""}
    state = {"overflow": False}
    process = subprocess.Popen(  # noqa: S603 - validated argv with an absolute executable
        argv,
        shell=False,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=cwd,
        env=env,
        start_new_session=True,
    )

    def drain(name: str, stream: BinaryIO) -> None:
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(stream.fileno(), min(65_536, byte_cap + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > byte_cap:
                state["overflow"] = True
                break
        collected[name] = b"".join(chunks)
        stream.close()

    timed_out = False
    try:
        if process.stdout is None or process.stderr is None:  # both pipes were requested
            raise AssertionError("unreachable: Popen(stdout=PIPE, stderr=PIPE)")
        readers = [
            threading.Thread(target=drain, args=(name, stream), daemon=True)
            for name, stream in (("stdout", process.stdout), ("stderr", process.stderr))
        ]
        deadline = time.monotonic() + timeout_seconds
        for reader in readers:
            reader.start()
        while not state["overflow"]:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timed_out = True
                break
            try:
                process.wait(timeout=min(0.05, remaining))
                break
            except subprocess.TimeoutExpired:
                pass
    finally:
        _kill_group(process)
    for reader in readers:
        reader.join(timeout=5)
    if state["overflow"]:
        raise OutputTooLarge
    if timed_out:
        raise subprocess.TimeoutExpired(argv, timeout_seconds)
    if any(reader.is_alive() for reader in readers):
        raise OSError("the command left a process holding its output open after it exited")
    return subprocess.CompletedProcess(argv, process.returncode, collected["stdout"], collected["stderr"])


def _kill_group(process: subprocess.Popen[bytes]) -> None:
    """Kill the whole process group: the command dies with its children, so a piped grandchild
    cannot hold the output pipes open past the kill (the repository is POSIX-only, ADR-0032).

    `start_new_session=True` made the command's pid the group id, and a group id stays valid while
    any member lives — including after the command itself was reaped — so the pid is used directly;
    `os.getpgid` fails on a reaped leader and would turn the kill into a no-op.
    """
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(process.pid, signal.SIGKILL)
    process.wait()
