"""The command runner's contract (ADR-0088): what a command receives, and how its run ends."""

import errno
import fcntl
import os
import signal
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from pathlib import Path
from unittest.mock import Mock

import pytest

from jev_judge_mcp import process
from jev_judge_mcp.process import OutputTooLarge, capture

ENV = {"PATH": os.environ["PATH"], "LC_ALL": "C"}


def run(argv: Sequence[str], *, timeout: float = 10, cap: int = 100_000) -> subprocess.CompletedProcess[bytes]:
    return capture(argv, env=ENV, timeout_seconds=timeout, byte_cap=cap)


def sh(script: str, **options: float) -> subprocess.CompletedProcess[bytes]:
    return run(["/bin/sh", "-c", script], **options)  # pyright: ignore[reportArgumentType]


def open_descriptors() -> int:
    return len(os.listdir("/dev/fd"))


def gone(pid: int) -> bool:
    """Dead, or a zombie waiting on a parent that does not reap (PID 1 in some containers)."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    try:
        return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0] == "Z"
    except (FileNotFoundError, ProcessLookupError):
        # On Linux the child was reaped between the kill and the read (open gives ENOENT, read gives ESRCH).
        # macOS has no /proc, so a live pid stays not gone there.
        return Path("/proc").is_dir()


@pytest.mark.parametrize("vanished", [FileNotFoundError, ProcessLookupError])
def test_gone_counts_a_child_reaped_between_the_probe_and_the_read(
    monkeypatch: pytest.MonkeyPatch, vanished: type[OSError]
) -> None:
    """On Linux the child can be reaped after kill(pid, 0) succeeds and before /proc/<pid>/stat is read."""
    assert not gone(os.getpid())

    def reaped(self: Path, *args: object, **kwargs: object) -> str:
        raise vanished(errno.ENOENT, "reaped", str(self))

    def proc_exists(self: Path) -> bool:
        return True

    monkeypatch.setattr(Path, "read_text", reaped)
    monkeypatch.setattr(Path, "is_dir", proc_exists)  # a /proc filesystem exists
    assert gone(os.getpid())


def wait_until(condition: Callable[[], bool], seconds: float = 3) -> bool:
    deadline = time.monotonic() + seconds
    while not condition():
        if time.monotonic() > deadline:
            return False
        time.sleep(0.02)
    return True


@pytest.fixture
def launched(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[int]]:
    """The pids every capture in the test started."""
    pids: list[int] = []
    launch = process._launch  # pyright: ignore[reportPrivateUsage]

    def recording(argv: Sequence[str], env: Mapping[str, str], stdout: int, stderr: int) -> tuple[int, object]:
        pid, reap = launch(argv, env, stdout, stderr)
        pids.append(pid)
        return pid, reap

    monkeypatch.setattr(process, "_launch", recording)
    yield pids


def assert_reaped(pids: list[int]) -> None:
    assert pids
    for pid in pids:
        with pytest.raises(ChildProcessError):
            os.waitpid(pid, os.WNOHANG)


# --- what a command receives --------------------------------------------------------------


def test_output_streams_and_exit_status_are_captured() -> None:
    result = sh("printf out; printf err >&2; exit 3")
    assert (result.stdout, result.stderr, result.returncode) == (b"out", b"err", 3)


def test_a_signal_death_is_a_negative_status() -> None:
    assert sh("kill -TERM $$").returncode == -15


def test_the_servers_stdin_never_reaches_the_command(tmp_path: Path) -> None:
    """The server's stdin is the MCP channel, an open pipe; a command reading it would block until
    the deadline. pytest's own fd 0 is /dev/null, so the server runs in a child with a real pipe."""
    report = tmp_path / "report"
    script = (
        "import os; r, w = os.pipe(); os.dup2(r, 0);"
        "from jev_judge_mcp.process import capture;"
        "res = capture(['cat'], env={'PATH': os.environ['PATH']}, timeout_seconds=5, byte_cap=100);"
        f"open({str(report)!r}, 'w').write(repr((res.stdout, res.returncode)))"
    )
    subprocess.run([sys.executable, "-c", script], check=True, timeout=30)
    assert report.read_text() == repr((b"", 0))


def test_a_command_leads_its_own_session() -> None:
    probe = "import os; print(os.getsid(0) == os.getpgid(0) == os.getpid())"
    assert run([sys.executable, "-c", probe]).stdout == b"True\n"


def test_sigxfsz_is_default_in_the_command(tmp_path: Path) -> None:
    """Python ignores SIGXFSZ; a command that inherited that would see a write error, not the signal."""
    result = sh(f"ulimit -f 1; head -c 8192 /dev/zero > {tmp_path / 'big'}")
    assert result.returncode in (-signal.SIGXFSZ, 128 + signal.SIGXFSZ)


@pytest.mark.skipif(sys.platform != "darwin", reason="Linux Popen keeps the caller's mask, as before")
def test_the_macos_command_starts_with_an_empty_signal_mask() -> None:
    result: list[int] = []

    def blocked_caller() -> None:
        signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGTERM})
        result.append(sh("kill -TERM $$").returncode)

    thread = threading.Thread(target=blocked_caller)
    thread.start()
    thread.join()
    assert result == [-signal.SIGTERM]


def test_sigpipe_is_default_in_the_command() -> None:
    """Python ignores SIGPIPE; a command that inherited that would report a broken pipe instead of dying."""
    result = sh("yes | head -n 1")
    assert (result.stdout, result.stderr, result.returncode) == (b"y\n", b"", 0)


def test_a_bare_program_resolves_on_the_commands_own_path(tmp_path: Path) -> None:
    tool = tmp_path / "fixture-tool"
    tool.write_text("#!/bin/sh\necho tool-ran\n")
    tool.chmod(0o700)
    found = capture(["fixture-tool"], env={"PATH": str(tmp_path)}, timeout_seconds=10, byte_cap=100)
    assert found.stdout == b"tool-ran\n"
    with pytest.raises(FileNotFoundError):  # on the server's PATH, not on the command's
        capture(["sh", "-c", "true"], env={"PATH": str(tmp_path)}, timeout_seconds=10, byte_cap=100)


@pytest.mark.parametrize(
    ("argv", "env"),
    [(["/bin/echo", "a\0b"], ENV), (["/bin/echo"], {**ENV, "A=B": "x"}), (["/bin/echo"], {**ENV, "A": "x\0y"})],
    ids=["argument", "name", "value"],
)
def test_an_argument_or_variable_that_cannot_pass_raises_value_error(argv: list[str], env: dict[str, str]) -> None:
    before = open_descriptors()
    with pytest.raises(ValueError):
        capture(argv, env=env, timeout_seconds=10, byte_cap=100)
    assert open_descriptors() == before


def test_a_command_receives_no_descriptor_but_its_own() -> None:
    """Other threads keep creating sockets and pipes, which on macOS are inheritable for an instant,
    and the server holds a descriptor marked inheritable; no command sees either."""
    marked_read, write_end = os.pipe()
    marked_write = fcntl.fcntl(write_end, fcntl.F_DUPFD, 64)  # inheritable, and above any fd ls opens
    os.close(write_end)
    baseline = run(["/bin/ls", "/dev/fd"]).stdout
    stop = threading.Event()

    def churn() -> None:
        while not stop.is_set():
            ends = os.pipe()
            socket.socket().close()
            for fd in ends:
                os.close(fd)

    threads = [threading.Thread(target=churn) for _ in range(3)]
    for thread in threads:
        thread.start()
    try:
        listings = [run(["/bin/ls", "/dev/fd"]).stdout for _ in range(300)]
    finally:
        stop.set()
        for thread in threads:
            thread.join()
        os.close(marked_read)
        os.close(marked_write)
    assert str(marked_write).encode() not in baseline.split()
    assert [listing for listing in listings if listing != baseline] == []


def test_a_closed_server_stdout_still_routes_the_commands_output(tmp_path: Path) -> None:
    """With fds 0 and 1 closed, the first pipe's write end is fd 1; dup2 onto itself would leave it
    close-on-exec and the command's stdout closed."""
    report = tmp_path / "report"
    script = (
        "import os, sys; os.close(0); os.close(1);"
        "from jev_judge_mcp.process import capture;"
        f"r = capture(['/bin/echo', 'hi'], env={{}}, timeout_seconds=10, byte_cap=100);"
        f"open({str(report)!r}, 'w').write(repr((r.stdout, r.returncode)))"
    )
    subprocess.run([sys.executable, "-c", script], check=True, timeout=30)
    assert report.read_text() == repr((b"hi\n", 0))


@pytest.mark.skipif(sys.platform != "darwin", reason="the libc spawn binding is macOS-only")
@pytest.mark.parametrize("failure", ["none", "configure", "spawn"])
def test_spawn_attributes_are_destroyed_on_every_path(failure: str, monkeypatch: pytest.MonkeyPatch) -> None:
    from jev_judge_mcp import darwin_spawn

    destroyed = {name: Mock(wraps=getattr(darwin_spawn, name)) for name in ("_attr_destroy", "_actions_destroy")}
    for name, wrapper in destroyed.items():
        monkeypatch.setattr(darwin_spawn, name, wrapper)
    if failure == "configure":
        monkeypatch.setattr(darwin_spawn, "_actions_adddup2", Mock(return_value=errno.EBADF))
    program = "/nonexistent/fixture-program" if failure == "spawn" else "/usr/bin/true"
    if failure == "none":
        assert run([program]).returncode == 0
    else:
        with pytest.raises(OSError):
            run([program])
    assert {name: wrapper.call_count for name, wrapper in destroyed.items()} == dict.fromkeys(destroyed, 1)


# --- how a run ends -----------------------------------------------------------------------


def test_runs_leave_no_descriptor_or_unreaped_command(launched: list[int]) -> None:
    before = open_descriptors()
    for _ in range(30):
        assert sh("printf x").stdout == b"x"
    assert open_descriptors() == before
    assert_reaped(launched)


def test_concurrent_runs_keep_their_own_output() -> None:
    results: dict[int, bytes] = {}

    def one(index: int) -> None:
        results[index] = sh(f"printf {index}").stdout

    threads = [threading.Thread(target=one, args=(index,)) for index in range(16)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert results == {index: str(index).encode() for index in range(16)}


def test_a_missing_program_raises_and_leaks_nothing() -> None:
    before = open_descriptors()
    for _ in range(20):
        with pytest.raises(FileNotFoundError):
            run(["/nonexistent/fixture-program"])
    assert open_descriptors() == before


def test_a_failed_pipe_closes_the_descriptors_already_opened(monkeypatch: pytest.MonkeyPatch) -> None:
    before = open_descriptors()
    pipe = os.pipe
    created: list[tuple[int, int]] = []

    def second_fails() -> tuple[int, int]:
        if created:
            raise OSError(errno.EMFILE, "fixture pipe error")
        created.append(pipe())
        return created[-1]

    monkeypatch.setattr(os, "pipe", second_fails)
    with pytest.raises(OSError, match="fixture pipe error"):
        sh("true")
    monkeypatch.undo()
    assert created
    assert open_descriptors() == before


def test_a_failed_exit_watch_kills_and_reaps_the_command(launched: list[int], monkeypatch: pytest.MonkeyPatch) -> None:
    before = open_descriptors()
    monkeypatch.setattr(process, "_exit_watch", Mock(side_effect=OSError("fixture watch error")))
    with pytest.raises(OSError, match="fixture watch error"):
        sh("sleep 30")
    assert open_descriptors() == before
    assert_reaped(launched)


def test_a_command_that_exits_before_its_watch_starts_still_completes(monkeypatch: pytest.MonkeyPatch) -> None:
    watch = process._exit_watch  # pyright: ignore[reportPrivateUsage]

    def late(pid: int) -> tuple[int | None, Callable[[], None]]:
        time.sleep(0.3)
        return watch(pid)

    monkeypatch.setattr(process, "_exit_watch", late)
    result = sh("printf done")
    assert (result.stdout, result.returncode) == (b"done", 0)


def test_a_timeout_kills_the_whole_group(tmp_path: Path, launched: list[int]) -> None:
    marker = tmp_path / "grandchild"
    before = open_descriptors()
    started = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        sh(f"sleep 30 & echo $! > {marker}; sleep 30", timeout=1)
    assert time.monotonic() - started < 3  # no drain wait after a timeout
    assert wait_until(lambda: gone(int(marker.read_text())))
    assert open_descriptors() == before
    assert_reaped(launched)


def test_a_flood_is_killed_at_the_cap(launched: list[int]) -> None:
    before = open_descriptors()
    started = time.monotonic()
    with pytest.raises(OutputTooLarge):
        run(["yes"], cap=1_000, timeout=30)
    assert time.monotonic() - started < 5
    assert open_descriptors() == before
    assert_reaped(launched)


def test_a_background_child_does_not_hold_a_finished_run(tmp_path: Path) -> None:
    marker = tmp_path / "grandchild"
    started = time.monotonic()
    result = sh(f"sleep 30 & echo $! > {marker}; echo first; echo second")
    assert result.stdout == b"first\nsecond\n"
    assert time.monotonic() - started < 3  # the run ends at the command's exit, not the pipes' close
    assert wait_until(lambda: gone(int(marker.read_text())))


def test_output_held_by_an_escaped_process_is_an_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(process, "DRAIN_GRACE_SECONDS", 0.3)
    marker = tmp_path / "escaped"
    script = (
        "import subprocess;"
        "p = subprocess.Popen(['sleep', '30'], start_new_session=True);"
        f"open({str(marker)!r}, 'w').write(str(p.pid)); print('parent done')"
    )
    started = time.monotonic()
    try:
        with pytest.raises(OSError, match="holding its output open"):
            run([sys.executable, "-c", script])
        assert time.monotonic() - started < 5
    finally:
        if marker.exists():
            os.kill(int(marker.read_text()), 9)
