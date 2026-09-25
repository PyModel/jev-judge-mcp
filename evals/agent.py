"""One agent run in a throwaway sandbox, shared by the P8 pilot and the bench.

`run_agent` owns the sandbox (a fresh workdir), the 0600 MCP config in a private dir, the process and
its timeout, the stream-json trace, cleanup, and secret scrubbing. Stdout is read under a byte cap;
past the cap the run raises inside the same `try` that scrubs. Stderr uses that cap too, but bytes
past it are dropped while the pipe is still drained, and one truncation line is appended. A timeout
SIGKILLs the agent's process group, which is a new session so the study is not in it. It knows nothing
of arms, cases, spend, or scoring: callers pass the command and the config, and grade inside the `with`
block while the workdir
still exists. On leaving the block, however it is left, both temp dirs are gone and every file under
`run_dir` is scrubbed of the secret.

The agent's environment is `base_env` updated with `command.env`, then isolated: `HOME`,
`PI_CODING_AGENT_DIR`, and `TMPDIR` are replaced with private directories inside the run's sandbox,
and only `PRIVATE_AGENT_FILES` is copied into the private agent dir, so no user MCP config, adapter
cache, settings, or instructions reach the agent (`_isolated_env`). Nothing is read from this
process's environment below that boundary.
"""

import json
import os
import shutil
import signal
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable, Generator, Iterable, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, BinaryIO

from evals.ab import stream

REDACTED = b"[REDACTED]"

# The model client's allowlist, and nothing else, crosses into the run's private pi agent dir.
# Credentials and the model catalog are required to reach the provider; every other file a real
# agent dir carries — mcp.json, mcp-cache.json, settings.json, extensions/, AGENTS.md — must not
# reach the agent, so the run's own --mcp-config stays the only MCP config it can see.
PRIVATE_AGENT_FILES = ("auth.json", "models.json", "models-store.json")

# One agent transcript. Past this, stdout raises inside `run_agent`'s try, so the finally scrub
# still runs and the caller's `except Exception` can book the run. Stderr keeps this many bytes,
# drops the rest, and appends `STDERR_TRUNCATED_LINE`.
STDOUT_CAP_BYTES = 64 * 1024 * 1024
STDERR_TRUNCATED_LINE = "[stderr truncated]"

_PIPE_READ = 65_536


@dataclass(frozen=True)
class AgentCommand:
    """How to start the agent: live `claude` and the offline stub differ only here."""

    argv: Callable[[Path], Sequence[str]]
    """The full command line, given the path of the MCP config the run writes."""
    timeout_s: float
    env: Mapping[str, str] = field(default_factory=dict[str, str])
    """Applied over the run's `base_env`."""


@dataclass(frozen=True)
class AgentRunResult:
    argv: tuple[str, ...]
    workdir: Path
    """The sandbox; it exists only inside the `run_agent` block."""
    stdout: str
    stderr: str
    returncode: int | None
    """None when the run timed out."""
    wall_s: float
    trace: stream.Trace
    status: str
    """`ok`, or `failed: ...` for a timeout, a nonzero exit, or a result that is not a success."""


def secret_scrub(root: Path, secret: str) -> None:
    if not secret:
        raise ValueError("the secret to scrub must be non-empty")
    needle = secret.encode()
    for path in root.rglob("*"):
        if path.is_file():
            data = path.read_bytes()
            if needle in data:
                path.write_bytes(data.replace(needle, REDACTED))


def _text(output: str | bytes | None) -> str:
    return output.decode(errors="replace") if isinstance(output, bytes) else (output or "")


def _status(returncode: int | None, trace: stream.Trace, timeout_s: float) -> str:
    if returncode is None:
        return f"failed: timeout after {timeout_s}s"
    if returncode != 0 or trace.result_field("subtype") != "success" or trace.result_field("is_error"):
        return f"failed: rc={returncode} subtype={trace.result_field('subtype')}"
    return "ok"


class _StdoutCapExceeded(OSError):
    """The agent wrote more than `STDOUT_CAP_BYTES`. An `OSError`, so the study's `except Exception` books it."""

    captured_stdout: str
    captured_stderr: str

    def __init__(self, stdout: str, stderr: str) -> None:
        super().__init__(f"agent stdout exceeded {STDOUT_CAP_BYTES} bytes")
        self.captured_stdout = stdout
        self.captured_stderr = stderr


class _IncompleteTranscript(RuntimeError):
    """An output reader stayed alive past its join, or never stopped at all: closing the
    pipes discards whatever the kernel still buffered, so the captured transcript is
    incomplete. Carries what was read, so the run's evidence can be persisted before the
    raise — a silently partial transcript is never returned."""

    captured_stdout: str
    captured_stderr: str

    def __init__(self, stdout: str, stderr: str) -> None:
        super().__init__(
            "agent output readers did not stop in time: buffered output was discarded and "
            "the captured transcript is incomplete"
        )
        self.captured_stdout = stdout
        self.captured_stderr = stderr


def _kill_group(proc: subprocess.Popen[bytes]) -> None:
    """SIGKILL the agent's process group. The agent is started with `start_new_session`, so this group
    is not the study's. `Popen.kill` signals only that pid and would leave the relay and its server child."""
    pid = proc.pid
    if pid <= 0:
        raise RuntimeError("refusing to signal process group 0")
    try:
        os.killpg(pid, signal.SIGKILL)
    except ProcessLookupError:
        return
    except PermissionError:
        # macOS returns EPERM once the leader is dying, and waitpid can miss that zombie for a
        # few milliseconds. A leader that is still alive after the wait is a real refusal.
        deadline = time.monotonic() + 1
        while proc.poll() is None:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.01)


def _write_logs(run_dir: Path, stdout: str, stderr: str) -> None:
    (run_dir / "stream.jsonl").write_text(stdout, encoding="utf-8")
    (run_dir / "claude.stderr").write_text(stderr, encoding="utf-8")


def _with_stderr_marker(stderr: str) -> str:
    """One trailing line. The kept prefix is unchanged apart from a newline before the marker."""
    if stderr and not stderr.endswith("\n"):
        stderr += "\n"
    return f"{stderr}{STDERR_TRUNCATED_LINE}\n"


def _read_pipe(
    pipe: BinaryIO,
    chunks: list[bytes],
    *,
    cap: int,
    stop: bool,
    flag: threading.Event,
    proc: subprocess.Popen[bytes],
) -> None:
    """Drain `pipe`, keeping at most `cap` bytes. Stdout (`stop`) kills the group and returns.
    Stderr discards the rest and reads to EOF so a full pipe cannot stall the agent."""
    total = 0
    discarding = False
    while True:
        try:
            chunk = pipe.read(_PIPE_READ)
        except (OSError, ValueError):
            return
        if chunk == b"":
            return
        if discarding:
            continue
        room = cap - total
        if len(chunk) <= room:
            chunks.append(chunk)
            total += len(chunk)
            continue
        if room > 0:
            chunks.append(chunk[:room])
        flag.set()
        if stop:
            _kill_group(proc)
            return
        discarding = True


def _capture(
    argv: Sequence[str],
    *,
    cwd: Path,
    env: Mapping[str, str],
    timeout_s: float,
    cap: int,
) -> tuple[str, str, int | None]:
    """Run `argv` in its own session. Returns `(stdout, stderr, returncode)`; `returncode` is None on
    timeout. Raises `_StdoutCapExceeded` when stdout passes `cap`."""
    proc = subprocess.Popen(
        argv,
        cwd=cwd,
        env=dict(env),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=0,
        start_new_session=True,
    )
    stdout_pipe = proc.stdout
    stderr_pipe = proc.stderr
    assert stdout_pipe is not None and stderr_pipe is not None
    out_chunks: list[bytes] = []
    err_chunks: list[bytes] = []
    exceeded = threading.Event()
    err_truncated = threading.Event()
    out_thread = threading.Thread(
        target=_read_pipe,
        args=(stdout_pipe, out_chunks),
        kwargs={"cap": cap, "stop": True, "flag": exceeded, "proc": proc},
    )
    err_thread = threading.Thread(
        target=_read_pipe,
        args=(stderr_pipe, err_chunks),
        kwargs={"cap": cap, "stop": False, "flag": err_truncated, "proc": proc},
    )
    out_thread.start()
    err_thread.start()
    timed_out = False
    incomplete = False
    deadline = time.monotonic() + timeout_s
    try:
        while proc.poll() is None and not exceeded.is_set():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timed_out = True
                _kill_group(proc)
                break
            out_thread.join(timeout=min(0.05, remaining))
        if exceeded.is_set():
            _kill_group(proc)
    finally:
        out_thread.join(timeout=5)
        err_thread.join(timeout=5)
        if out_thread.is_alive() or err_thread.is_alive():
            # The reader is still draining: the pipes hold bytes nobody has read. Closing the
            # read end discards them, so the transcript would come back silently partial.
            # Mark the capture incomplete (the raise happens after cleanup) instead.
            incomplete = True
            stdout_pipe.close()
            stderr_pipe.close()
            out_thread.join(timeout=5)
            err_thread.join(timeout=5)
        if proc.poll() is None:
            _kill_group(proc)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            _kill_group(proc)
            proc.wait(timeout=5)
        if not stdout_pipe.closed:
            stdout_pipe.close()
        if not stderr_pipe.closed:
            stderr_pipe.close()
    stdout, stderr = _text(b"".join(out_chunks)), _text(b"".join(err_chunks))
    if out_thread.is_alive() or err_thread.is_alive():
        raise _IncompleteTranscript(stdout, stderr)
    if incomplete:
        raise _IncompleteTranscript(stdout, stderr)
    if err_truncated.is_set():
        stderr = _with_stderr_marker(stderr)
    if exceeded.is_set():
        raise _StdoutCapExceeded(stdout, stderr)
    if timed_out:
        return stdout, stderr, None
    return stdout, stderr, proc.returncode


def _isolated_env(base_env: Mapping[str, str], sandbox: Path) -> dict[str, str]:
    """A private HOME, pi agent dir, and TMPDIR inside `sandbox`, with the model client's allowlist.

    A recorded smoke run had a bench agent wander out of its sandbox: it read `~/.pi/agent/mcp.json`
    and `~/.pi/agent/.env` and ran `uv sync` inside a real checkout. The agent now gets a HOME that
    contains nothing user-specific and a pi agent dir (`PI_CODING_AGENT_DIR`) holding only
    `PRIVATE_AGENT_FILES`, so no user MCP config, adapter cache, settings, extensions, or
    instructions reach it. Copied from the caller's real agent dir — `PI_CODING_AGENT_DIR` in
    `base_env`, else `<HOME>/.pi/agent` — resolved before the override is applied. The sandbox is
    deleted with the run; the copies never outlive it.
    """
    home, agent_dir, tmp = (sandbox / name for name in ("home", "agent", "tmp"))
    for directory in (home, agent_dir, tmp):
        directory.mkdir(mode=0o700, exist_ok=True)
    real = base_env.get("PI_CODING_AGENT_DIR") or (
        str(Path(base_env["HOME"]) / ".pi" / "agent") if base_env.get("HOME") else ""
    )
    if real:
        for name in PRIVATE_AGENT_FILES:
            source = Path(real) / name
            if source.is_file():
                target = agent_dir / name
                target.write_bytes(source.read_bytes())
                os.chmod(target, 0o600)
    return {"HOME": str(home), "PI_CODING_AGENT_DIR": str(agent_dir), "TMPDIR": str(tmp)}


@contextmanager
def run_agent(
    command: AgentCommand,
    *,
    mcp_config: Mapping[str, Any],
    base_env: Mapping[str, str],
    secret: str,
    run_dir: Path,
    prepare: Callable[[Path], None] | None = None,
    parse: Callable[[Iterable[str]], stream.Trace] | None = None,
) -> Generator[AgentRunResult]:
    """Run the agent once; yields its result while the workdir exists. Writes `stream.jsonl` and
    `claude.stderr` to `run_dir`. Stdout past the cap raises `OSError` after those files are written
    and before the yield. Stderr past the cap is cut, the pipe is drained, and one truncation line is
    appended; that does not fail the run. A timeout kills the agent's process group and yields
    `returncode=None`."""
    if not secret:
        raise ValueError("run_agent needs the non-empty secret to scrub")
    run_dir.mkdir(parents=True, exist_ok=True)
    workdir = Path(tempfile.mkdtemp(prefix="jev-agent-work-")).resolve()
    secret_dir = Path(tempfile.mkdtemp(prefix="jev-agent-cfg-"))
    try:
        if prepare is not None:
            prepare(workdir)
        config = secret_dir / "mcp.json"
        fd = os.open(config, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as sink:
            json.dump(mcp_config, sink)
        argv = tuple(command.argv(config))
        isolated = _isolated_env(base_env, secret_dir)
        started = time.perf_counter()
        try:
            stdout, stderr, returncode = _capture(
                argv,
                cwd=workdir,
                env={**base_env, **command.env, **isolated},
                timeout_s=command.timeout_s,
                cap=STDOUT_CAP_BYTES,
            )
        except _StdoutCapExceeded as exceeded:
            # Persist before the raise so the finally scrub still sees the secret on this path.
            shutil.rmtree(secret_dir, ignore_errors=True)
            _write_logs(run_dir, exceeded.captured_stdout, exceeded.captured_stderr)
            raise
        except _IncompleteTranscript as incomplete:
            # Same contract: persist what was read, then fail loudly. A silently partial
            # transcript would book an agent verdict on missing evidence.
            shutil.rmtree(secret_dir, ignore_errors=True)
            _write_logs(run_dir, incomplete.captured_stdout, incomplete.captured_stderr)
            raise
        wall = time.perf_counter() - started
        shutil.rmtree(secret_dir, ignore_errors=True)
        _write_logs(run_dir, stdout, stderr)
        trace = (parse or stream.parse)(stdout.splitlines())
        yield AgentRunResult(
            argv=argv,
            workdir=workdir,
            stdout=stdout,
            stderr=stderr,
            returncode=returncode,
            wall_s=wall,
            trace=trace,
            status=_status(returncode, trace, command.timeout_s),
        )
    finally:
        shutil.rmtree(secret_dir, ignore_errors=True)
        shutil.rmtree(workdir, ignore_errors=True)
        secret_scrub(run_dir, secret)
