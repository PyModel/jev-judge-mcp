"""Capture a command's output under one deadline and a per-stream byte bound (ADR-0088).

Every command runs in the server's current directory, in its own session, with stdin at /dev/null
and no descriptor but its two output pipes: none of the server's files or sockets reach it. The
calling thread waits on the pipes and on the command's exit together, so a run ends when the command
exits, not on a polling interval. The command is still unreaped when its process group is killed, so the
group id cannot have passed to another process.
"""

import contextlib
import fcntl
import os
import select
import selectors
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Mapping, Sequence

DRAIN_GRACE_SECONDS = 5.0
"""How long the output may stay open after the command exited and its group was killed. Only a process
that left the group for its own session can still hold it then."""


class OutputTooLarge(Exception):
    """A subprocess exceeded its capture bound."""


if sys.platform == "darwin":
    from jev_judge_mcp.darwin_spawn import spawn as _spawn

    def _launch(argv: Sequence[str], env: Mapping[str, str], stdout: int, stderr: int) -> tuple[int, Callable[[], int]]:
        pid = _spawn(argv, env, stdout, stderr)
        return pid, lambda: os.waitstatus_to_exitcode(os.waitpid(pid, 0)[1])

else:

    def _launch(argv: Sequence[str], env: Mapping[str, str], stdout: int, stderr: int) -> tuple[int, Callable[[], int]]:
        # Linux Popen starts the child with vfork and releases the GIL meanwhile; close_fds (the
        # default) closes every descriptor but the three below.
        process = subprocess.Popen(  # noqa: S603 - argv is validated by every caller; no shell
            argv, stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr, env=env, start_new_session=True
        )
        return process.pid, process.wait


if sys.platform == "linux":

    def _exit_watch(pid: int) -> tuple[int | None, Callable[[], None]]:
        """A pipe that turns readable once pid exits, and its release, which waits for the watcher.

        A thread waits with WNOWAIT, so the command stays unreaped. `os.pidfd_open` would need no
        thread, but some Linux builds of Python (python-build-standalone, which uv installs) lack it.
        """
        read_end, write_end = os.pipe()

        def wait() -> None:
            try:
                with contextlib.suppress(ChildProcessError):  # reaped elsewhere: close() reports it
                    os.waitid(os.P_PID, pid, os.WEXITED | os.WNOWAIT)
            finally:
                os.close(write_end)

        watcher = threading.Thread(target=wait, name=f"jev-exit-{pid}", daemon=True)
        try:
            watcher.start()
        except BaseException:
            os.close(read_end)
            os.close(write_end)
            raise

        def release() -> None:
            watcher.join()  # close() killed the group first, so the wait has returned or is returning
            os.close(read_end)

        return read_end, release

else:

    def _exit_watch(pid: int) -> tuple[int | None, Callable[[], None]]:
        """A kqueue readable once pid exits, and its release; None when pid already exited."""
        queue = select.kqueue()
        try:
            queue.control(
                [
                    select.kevent(
                        pid, select.KQ_FILTER_PROC, select.KQ_EV_ADD | select.KQ_EV_ONESHOT, select.KQ_NOTE_EXIT
                    )
                ],
                0,
            )
        except ProcessLookupError:  # an exited, unreaped child refuses the registration
            queue.close()
            return None, lambda: None
        except BaseException:
            queue.close()
            raise
        return queue.fileno(), queue.close


def capture(
    argv: Sequence[str], *, env: Mapping[str, str], timeout_seconds: float, byte_cap: int
) -> subprocess.CompletedProcess[bytes]:
    """Run argv. Raises `subprocess.TimeoutExpired` past the deadline, `OutputTooLarge` past byte_cap
    on either stream, and `OSError` when output stays open after the command exited; the command's
    process group is killed before any of them is raised."""
    deadline = time.monotonic() + timeout_seconds
    child = _Child(argv, env)
    try:
        stdout, stderr = _collect(child, deadline, byte_cap)
    except _Timeout:
        raise subprocess.TimeoutExpired(argv, timeout_seconds) from None
    finally:
        returncode = child.close()
    return subprocess.CompletedProcess(argv, returncode, stdout, stderr)


class _Timeout(Exception):
    pass


class _Child:
    """A started command: its pid, the read ends of its output pipes, and a watch on its exit."""

    def __init__(self, argv: Sequence[str], env: Mapping[str, str]) -> None:
        opened: list[int] = []
        try:
            self.stdout, stdout_end = _pipe(opened)
            self.stderr, stderr_end = _pipe(opened)
            self.pid, self._reap = _launch(argv, env, stdout_end, stderr_end)
        except BaseException:
            for fd in opened:
                os.close(fd)
            raise
        os.close(stdout_end)
        os.close(stderr_end)
        try:
            self.exit_fd, self._release_watch = _exit_watch(self.pid)
        except BaseException:
            self.kill()
            self._reap()
            os.close(self.stdout)
            os.close(self.stderr)
            raise

    def kill(self) -> None:
        """Kill the whole process group (start_new_session made the pid its id), so a piped grandchild
        cannot hold the output open past the command (the repository is POSIX-only, ADR-0032). macOS
        refuses a group whose only member has exited with EPERM."""
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(self.pid, signal.SIGKILL)

    def close(self) -> int:
        """Kill the group, then reap the command and release every descriptor; return its exit code."""
        try:
            self.kill()
            self._release_watch()
            return self._reap()
        finally:
            os.close(self.stdout)
            os.close(self.stderr)


def _collect(child: _Child, deadline: float, byte_cap: int) -> tuple[bytes, bytes]:
    output = {child.stdout: bytearray(), child.stderr: bytearray()}
    with selectors.DefaultSelector() as selector:
        for fd in output:
            selector.register(fd, selectors.EVENT_READ)
        if child.exit_fd is not None:
            selector.register(child.exit_fd, selectors.EVENT_READ)
            while child.exit_fd in selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise _Timeout
                for key, _ in selector.select(remaining):
                    if key.fd == child.exit_fd:
                        selector.unregister(key.fd)
                    else:
                        _read(selector, key.fd, output[key.fd], byte_cap)
        child.kill()  # the command exited; what is left of its group may still hold the pipes
        grace = time.monotonic() + DRAIN_GRACE_SECONDS
        while selector.get_map():
            remaining = grace - time.monotonic()
            if remaining <= 0:
                raise OSError("the command left a process holding its output open after it exited")
            for key, _ in selector.select(remaining):
                _read(selector, key.fd, output[key.fd], byte_cap)
    return bytes(output[child.stdout]), bytes(output[child.stderr])


def _read(selector: selectors.BaseSelector, fd: int, buffer: bytearray, byte_cap: int) -> None:
    chunk = os.read(fd, min(65_536, byte_cap + 1 - len(buffer)))
    if not chunk:
        selector.unregister(fd)
        return
    buffer += chunk
    if len(buffer) > byte_cap:
        raise OutputTooLarge


def _pipe(opened: list[int]) -> tuple[int, int]:
    """A pipe, recording each descriptor it opens. A write end that landed on a closed stdout or stderr
    is moved above them, since dup2 onto its own number would leave it close-on-exec."""
    read_end, write_end = os.pipe()
    opened += (read_end, write_end)
    if write_end <= 2:
        moved = fcntl.fcntl(write_end, fcntl.F_DUPFD_CLOEXEC, 3)
        opened.append(moved)
        os.close(write_end)
        opened.remove(write_end)
        write_end = moved
    return read_end, write_end
