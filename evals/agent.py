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
cache, settings, or instructions reach the agent (`_isolated_env`). Claude Code's login has two
independent requirements, and either one missing makes every run die at startup with "Not logged in".
Sandboxing `HOME` (bff5450, 2026-09-23) empties the macOS default keychain search list, so a Claude
run (`login_keychain`) gets a symlink to `login.keychain-db` only; the directory stays in the sandbox.
A set `CLAUDE_CONFIG_DIR` fails the login even when that file is reachable (5a902c3 set it to the
sandbox), so it stays unset and Claude's project state lands in the sandbox. Pi never gets the link:
it logs in through the copied `auth.json`. The link is the same kind of exposure as that copy: a
Claude run can read the login keychain file through it. Nothing is read from this process's
environment below that boundary.
"""

import json
import os
import posixpath
import re
import shlex
import shutil
import signal
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable, Generator, Iterable, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, BinaryIO, cast

from evals.ab import stream

REDACTED = b"[REDACTED]"

# The model client's allowlist, and nothing else, crosses into the run's private pi agent dir.
# Credentials and the model catalog are required to reach the provider; every other file a real
# agent dir carries — mcp.json, mcp-cache.json, settings.json, extensions/, AGENTS.md — must not
# reach the agent, so the run's own --mcp-config stays the only MCP config it can see.
# auth.json is scoped, never copied whole: only the one provider entry the arm's model needs
# crosses (`auth_provider`). With no provider named, auth.json does not cross at all.
PRIVATE_AGENT_FILES = ("auth.json", "models.json", "models-store.json")

RELAY_LOG = "jev-calls.jsonl"
"""The recording proxy's log name. It is written inside the run sandbox — never under a host path
the agent could read in its own MCP config — and copied to the run's records when the run ends."""

# One agent transcript. Past this, stdout raises inside `run_agent`'s try, so the finally scrub
# still runs and the caller's `except Exception` can book the run. Stderr keeps this many bytes,
# drops the rest, and appends `STDERR_TRUNCATED_LINE`.
STDOUT_CAP_BYTES = 64 * 1024 * 1024
STDERR_TRUNCATED_LINE = "[stderr truncated]"

_PIPE_READ = 65_536

_TOP = "|".join(re.escape(name) for name in sorted(os.listdir("/")) if name.strip())
_ABS = re.compile(rf"(?<![\w.$~*])/(?:{_TOP})(?:/[^\s\"'`;|&<>(){{}}\[\],]*)?(?![\w.-])")
"""An absolute path whose first segment exists at `/` on this host: `/Users/..`, `/etc/..`, `/Library`.
Floor division, `a/n`, and glob fragments are not paths. Built at import, so a container's own `/`
defines its own list."""
_TILDE_USER = re.compile(r"(?:^|[\s=:])~[A-Za-z_][\w.-]*")
_BARE_ROOT = re.compile(r"(?:^|[\s;&|(])/\*?(?=$|[\s;&|)])")
_SECRET_VAR = re.compile(r"\$\{?(?:JEV_MCP_KEY_FILE|TYPESAFE_API_KEY)\b")
_SEGMENTS = re.compile(r"&&|\|\||;|\||\n")
_INPUT_ALLOW = ("/dev/null", "/dev/stdin", "/dev/stdout", "/dev/stderr", "/dev/tty")
"""Streams a command may name."""
_SYSTEM_BIN = ("/bin", "/usr/bin")
"""Executables an agent may name (`/usr/bin/env python3`); nothing user-specific lives there."""
_CREDENTIAL_NAME = re.compile(
    r"(?:^|/)(?:auth\.json|models\.json|typesafe\.key|id_rsa|id_ed25519|\.env|credentials(?:\.json)?"
    r"|login\.keychain-db|\.netrc|\.pypirc)$",
    re.IGNORECASE,
)
_WRITTEN_KEYS = frozenset({"content", "newText", "new_string", "oldText", "old_string", "text"})
"""Write-tool payload keys: file text the agent authored, not a path it touched."""


@dataclass(frozen=True)
class Boundary:
    workdir: str
    roots: tuple[str, ...]
    """workdir and sandbox, each in its created and resolved spelling."""
    secrets: tuple[str, ...]
    """Paths inside the sandbox that hold credentials: the agent dir and the keyfile."""
    runtime: tuple[str, ...]
    """Prefixes a tool result may name: the agent's own interpreter and runtime installs."""
    env: Mapping[str, str]
    """The agent's HOME/TMPDIR/PI_CODING_AGENT_DIR, for `$VAR` and `~` expansion."""


def _both(path: str) -> tuple[str, ...]:
    """Every spelling of one directory: as created, resolved, and without macOS's /private prefix."""
    spellings = {path.rstrip("/"), os.path.realpath(path).rstrip("/")}
    spellings |= {s.removeprefix("/private") for s in spellings if s.startswith("/private/")}
    return tuple(sorted(spellings))


def boundary(workdir: str, sandbox: str, env: Mapping[str, str], runtime: Sequence[str]) -> Boundary:
    """Build once per run, BEFORE the sandbox is deleted (realpath needs it to exist)."""
    secrets = [p for base in _both(sandbox) for p in (f"{base}/agent", f"{base}/typesafe.key")]
    return Boundary(
        workdir=os.path.realpath(workdir),
        roots=(*_both(workdir), *_both(sandbox)),
        secrets=tuple(secrets),
        runtime=tuple(runtime),
        env=dict(env),
    )


@lru_cache(maxsize=4)
def runtime_prefixes(path_env: str) -> tuple[str, ...]:
    """The interpreter's prefixes and the dirs of python3/node/pi on the agent PATH.

    A tool result may name these without being an escape: a stdlib traceback, `which python3`, a
    node stack trace. Cached, so one probe subprocess per study, not per run.
    """
    if not path_env:
        return ()
    found: list[str] = []
    python = shutil.which("python3", path=path_env)
    if python:
        try:
            out = subprocess.run(
                [python, "-I", "-c", "import sys;print(sys.base_prefix);print(sys.prefix)"],
                capture_output=True,
                text=True,
                check=True,
                timeout=30,
            ).stdout.split()
            found += out
        except (subprocess.SubprocessError, OSError):
            pass
    for name in ("python3", "node", "pi"):
        where = shutil.which(name, path=path_env)
        if where:
            found += [os.path.dirname(where), os.path.dirname(os.path.dirname(os.path.realpath(where)))]
    return tuple(sorted({p.rstrip("/") for p in found if p and p != "/"}))


def _under(path: str, prefixes: Iterable[str]) -> bool:
    return any(path == p or path.startswith(p + "/") for p in prefixes)


def _expand(token: str, cwd: str, env: Mapping[str, str]) -> str | None:
    """Absolute normalized path for a path-like token, or None when it is not one."""
    for name in ("HOME", "TMPDIR", "PI_CODING_AGENT_DIR"):
        value = env.get(name)
        if value:
            token = token.replace("${" + name + "}", value).replace("$" + name, value)
    tilde = "~"
    if token == tilde or token.startswith(tilde + "/"):
        token = env.get("HOME", "/nonexistent-home") + token[1:]
    if not (token.startswith("/") or token.startswith(".") or "/" in token):
        return None
    return posixpath.normpath(token if token.startswith("/") else posixpath.join(cwd, token))


def _input_hit(command: str, box: Boundary, cwd0: str) -> str | None:
    if _BARE_ROOT.search(command):
        return "filesystem root target"
    if _TILDE_USER.search(command):
        return "another user's home (~user)"
    if _SECRET_VAR.search(command):
        return "credential variable"
    cwd = cwd0
    for segment in _SEGMENTS.split(command):
        try:
            words = shlex.split(segment, posix=True)
        except ValueError:
            words = segment.split()
        if words[:1] == ["cd"]:
            target = words[1] if len(words) > 1 else "~"
            cwd = (
                _expand(
                    target if "/" in target or target.startswith(("~", "$", ".")) else "./" + target,
                    cwd,
                    box.env,
                )
                or cwd
            )
            words = words[1:]
        for word in words:
            for inner in _ABS.findall(word) if not word.startswith("/") else []:
                path = posixpath.normpath(inner)
                if path not in _INPUT_ALLOW and not _under(path, (*box.roots, *box.runtime, *_SYSTEM_BIN)):
                    return f"path outside the boundary {path[:200]}"
            pieces = [word] if word.startswith("/") else re.split(r"[=:]", word)
            for piece in pieces:
                if ("/" in piece or "." in piece) and _CREDENTIAL_NAME.search(piece.rstrip("\"',;:")):
                    return f"credential read {posixpath.basename(piece)[:80]}"
                if piece.startswith("/") and not _ABS.match(piece):
                    continue
                path = _expand(piece, cwd, box.env)
                if path is None or path in _INPUT_ALLOW:
                    continue
                if _under(path, box.secrets) or _CREDENTIAL_NAME.search(path):
                    return f"credential read {posixpath.basename(path)[:80]}"
                if any(ch in path for ch in "*?[") and _under(posixpath.dirname(path), box.secrets):
                    return "credential glob"
                if not _under(path, (*box.roots, *box.runtime, *_SYSTEM_BIN)):
                    return f"path outside the boundary {path[:200]}"
    return None


def _strings(value: object, *, skip_written: bool) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        out: list[str] = []
        for key, item in cast(dict[str, object], value).items():
            if not (skip_written and key in _WRITTEN_KEYS):
                out += _strings(item, skip_written=skip_written)
        return out
    if isinstance(value, list):
        return [s for item in cast(list[object], value) for s in _strings(item, skip_written=skip_written)]
    return []


def _events(stdout: str) -> Iterable[tuple[str, list[str]]]:
    """(`input` | `result`, strings) per tool event, from Claude stream-json and Pi events."""
    for raw in stdout.splitlines():
        line = raw.strip()
        if not line.startswith("{"):
            continue
        try:
            event: object = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        payload = cast(dict[str, Any], event)
        kind = payload.get("type")
        if kind == "tool_execution_start":
            yield "input", _strings(payload.get("args"), skip_written=True)
        elif kind == "tool_execution_end":
            yield "result", _strings(payload.get("result"), skip_written=False)
        elif kind in ("assistant", "user"):
            wanted = "tool_use" if kind == "assistant" else "tool_result"
            message = cast(dict[str, Any], payload.get("message") or {})
            for block in cast(list[object], message.get("content") or []):
                if isinstance(block, dict) and cast(dict[str, Any], block).get("type") == wanted:
                    body = cast(dict[str, Any], block)
                    if kind == "assistant":
                        yield "input", _strings(body.get("input"), skip_written=True)
                    else:
                        yield "result", _strings(body.get("content"), skip_written=False)


def escape_scan(stdout: str, box: Boundary) -> str | None:
    """Why the run left its boundary, or None. First hit wins. A heuristic, not a boundary:
    obfuscated paths (base64, string concatenation inside a program) are only caught if their
    output names a host path; the confinement branch is what closes those."""
    cwd0 = box.workdir
    for side, texts in _events(stdout):
        for text in texts:
            if side == "input":
                hit = _input_hit(text, box, cwd0)
                if hit:
                    return f"escape: {hit}"
                continue
            for candidate in _ABS.findall(text):
                path = posixpath.normpath(candidate.rstrip(".:"))
                if not _under(path, (*box.roots, *box.runtime)) and path not in _INPUT_ALLOW:
                    return f"escape: host path in a tool result {path[:200]}"
    return None


@dataclass(frozen=True)
class AgentCommand:
    """How to start the agent: live `claude` and the offline stub differ only here."""

    argv: Callable[[Path], Sequence[str]]
    """The full command line, given the path of the MCP config the run writes."""
    timeout_s: float
    env: Mapping[str, str] = field(default_factory=dict[str, str])
    """Applied over the run's `base_env`."""
    login_keychain: bool = False
    """Claude runs only. Links `login.keychain-db` into the sandbox home. Off for Pi and every other agent."""


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
    escape: str | None = None
    """Why the run left its boundary (`escape_scan`), or None when the transcript stayed inside."""


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


class AgentSetupError(RuntimeError):
    """The sandbox could not be prepared. Not a run: nothing is booked."""


class AgentPreflightError(RuntimeError):
    """The preflight never reached the model. The message is the agent's own error, capped, with no secret."""

    cost_usd: float | None
    model: str | None

    def __init__(self, detail: str, *, cost_usd: float | None, model: str | None) -> None:
        super().__init__(detail)
        self.cost_usd = cost_usd
        self.model = model


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


def _scoped_auth(source: Path, provider: str) -> bytes | None:
    """`auth.json` reduced to the one provider entry, or None when there is nothing to copy.

    The real auth.json holds every provider the operator has ever logged in to. A run needs one.
    An unparsable file copies nothing: a broken credential file is a setup error, not an
    invitation to hand the agent the whole file.
    """
    try:
        entries: object = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(entries, dict) or provider not in entries:
        return None
    entry = cast(dict[str, Any], entries)[provider]
    return json.dumps({provider: entry}, indent=2).encode() + b"\n"


def _isolated_env(
    base_env: Mapping[str, str], sandbox: Path, login_keychain: bool, auth_provider: str | None = None
) -> dict[str, str]:
    """A private HOME, pi agent dir, and TMPDIR inside `sandbox`, with the model client's allowlist.

    A recorded smoke run had a bench agent wander out of its sandbox: it read `~/.pi/agent/mcp.json`
    and `~/.pi/agent/.env` and ran `uv sync` inside a real checkout. The agent now gets a HOME that
    contains nothing user-specific and a pi agent dir (`PI_CODING_AGENT_DIR`) holding only
    `PRIVATE_AGENT_FILES`, so no user MCP config, adapter cache, settings, extensions, or
    instructions reach it. Copied from the caller's real agent dir — `PI_CODING_AGENT_DIR` in
    `base_env`, else `<HOME>/.pi/agent` — resolved before the override is applied. The sandbox is
    deleted with the run; the copies never outlive it.

    Claude Code's login needs both of the following, and either one missing reports "Not logged in".
    Sandboxing `HOME` has done the first since bff5450 (2026-09-23): it empties the macOS default
    keychain search list. A set `CLAUDE_CONFIG_DIR` does the second even when the keychain file is
    reachable, which is what 5a902c3 added. So `CLAUDE_CONFIG_DIR` stays unset, and only a Claude run
    (`login_keychain`) gets `<home>/Library/Keychains/login.keychain-db` linked to the real file. The
    directory is the sandbox's, so the rest of the real keychain directory is not reachable through
    `HOME`. No credential is copied, read, or passed. The link is still an exposure: that Claude run
    can read the login keychain file through it, the same way a Pi run can read the copied `auth.json`.
    """
    home, agent_dir, tmp = (sandbox / name for name in ("home", "agent", "tmp"))
    for directory in (home, agent_dir, tmp):
        directory.mkdir(mode=0o700, exist_ok=True)
    real_home = base_env.get("HOME")
    real = base_env.get("PI_CODING_AGENT_DIR") or (str(Path(real_home) / ".pi" / "agent") if real_home else "")
    if real:
        for name in PRIVATE_AGENT_FILES:
            source = Path(real) / name
            if not source.is_file():
                continue
            target = agent_dir / name
            if name == "auth.json":
                scoped = _scoped_auth(source, auth_provider) if auth_provider else None
                if scoped is None:
                    continue
                target.write_bytes(scoped)
            else:
                target.write_bytes(source.read_bytes())
            os.chmod(target, 0o600)
    # Resolved from the real HOME before the override below replaces it. The directory stays ours;
    # only the login file is a link, so a refresh writes through to the real file and the link remains.
    if login_keychain and real_home:
        library = home / "Library"
        library.mkdir(mode=0o700)
        keychains = library / "Keychains"
        keychains.mkdir(mode=0o700)
        (keychains / "login.keychain-db").symlink_to(Path(real_home) / "Library" / "Keychains" / "login.keychain-db")
    return {
        "HOME": str(home),
        "PI_CODING_AGENT_DIR": str(agent_dir),
        "TMPDIR": str(tmp),
    }


@contextmanager
def run_agent(
    command: AgentCommand,
    *,
    mcp_config: Mapping[str, Any] | Callable[[Path], Mapping[str, Any]],
    base_env: Mapping[str, str],
    secret: str,
    run_dir: Path,
    prepare: Callable[[Path], None] | None = None,
    parse: Callable[[Iterable[str]], stream.Trace] | None = None,
    auth_provider: str | None = None,
    runtime: Sequence[str] = (),
) -> Generator[AgentRunResult]:
    """Run the agent once; yields its result while the workdir exists. Writes `stream.jsonl` and
    `claude.stderr` to `run_dir`. Stdout past the cap raises `OSError` after those files are written
    and before the yield. Stderr past the cap is cut, the pipe is drained, and one truncation line is
    appended; that does not fail the run. A timeout kills the agent's process group and yields
    `returncode=None`.

    `mcp_config` may be a callable of the run's private sandbox directory, so a study can place
    sandbox-only paths (the relay log, a scoped key file, copied server entry points) in the config
    the agent can read. `auth_provider` scopes the copied auth.json to that one provider's entry;
    None copies no auth.json at all. `runtime` is the agent runtime's path prefixes for the escape
    canary; empty computes them from `base_env`'s PATH (cached per study).
    """
    if not secret:
        raise ValueError("run_agent needs the non-empty secret to scrub")
    run_dir.mkdir(parents=True, exist_ok=True)
    workdir = Path(tempfile.mkdtemp(prefix="jev-agent-work-")).resolve()
    secret_dir = Path(tempfile.mkdtemp(prefix="jev-agent-cfg-"))
    try:
        if prepare is not None:
            prepare(workdir)
        resolved_config = mcp_config(secret_dir) if callable(mcp_config) else mcp_config
        config = secret_dir / "mcp.json"
        fd = os.open(config, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as sink:
            json.dump(resolved_config, sink)
        argv = tuple(command.argv(config))
        try:
            isolated = _isolated_env(base_env, secret_dir, command.login_keychain, auth_provider)
        except OSError as error:
            raise AgentSetupError(f"agent sandbox setup failed: {error}") from error
        # Before anything deletes the sandbox: the canary needs every spelling of both roots while
        # they still resolve.
        box = boundary(
            str(workdir),
            str(secret_dir),
            {**base_env, **command.env, **isolated},
            runtime or runtime_prefixes(base_env.get("PATH", "")),
        )
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
        for name in (RELAY_LOG, str(Path(RELAY_LOG).with_suffix(".stderr"))):
            relay = secret_dir / name
            if relay.is_file():
                shutil.copyfile(relay, run_dir / name)
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
            escape=escape_scan(stdout, box),
        )
    finally:
        shutil.rmtree(secret_dir, ignore_errors=True)
        shutil.rmtree(workdir, ignore_errors=True)
        secret_scrub(run_dir, secret)


def _usd(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def preflight_detail(run: AgentRunResult) -> str:
    """The run's status, or the provider error the Pi parser records. Claude's stream never sets that field."""
    failure = run.trace.result_field("server_error")
    if isinstance(failure, str) and failure.strip():
        return failure.splitlines()[0][:160]
    line = next((stripped for raw in run.stderr.splitlines() if (stripped := raw.strip())), "")
    return f"{run.status}; {line[:160]}" if line else run.status


PREFLIGHT_TIMEOUT_S = 180.0
"""One prompt to prove the agent can reach its model, before any paid run is booked."""


def write_preflight(out: Path, *, cost_usd: float | None, model: str | None) -> None:
    """Record this preflight's cost where a report can add it. Not a ledger booking, and not a run."""
    out.mkdir(parents=True, exist_ok=True)
    (out / "preflight.json").write_text(
        json.dumps({"total_cost_usd": cost_usd, "model": model}) + "\n",
        encoding="utf-8",
    )


def run_preflight(
    argv: Callable[[Path], Sequence[str]],
    *,
    base_env: Mapping[str, str],
    secret: str,
    mcp_config: Mapping[str, Any],
    login_keychain: bool,
    parse: Callable[[Iterable[str]], stream.Trace] | None = None,
    timeout_s: float = PREFLIGHT_TIMEOUT_S,
    auth_provider: str | None = None,
) -> AgentRunResult:
    """One prompt through `run_agent`'s own env. Raises `AgentPreflightError` when the model was not reached.

    The scratch dir is not the study's `out`, and this writes no ledger. `login_keychain` is true only
    for Claude; a Pi or third-party-model preflight passes false and gets no keychain path. The yielded
    workdir is gone by the time this returns: callers use the trace. `auth_provider` scopes the
    preflight's auth.json exactly like a run's.
    """
    from evals.ab.outcomes import reached_model

    scratch = Path(tempfile.mkdtemp(prefix="jev-agent-preflight-"))
    try:
        command = AgentCommand(argv=argv, timeout_s=timeout_s, login_keychain=login_keychain)
        with run_agent(
            command,
            mcp_config=mcp_config,
            base_env=base_env,
            secret=secret,
            run_dir=scratch,
            parse=parse,
            auth_provider=auth_provider,
        ) as run:
            cost = _usd(run.trace.result_field("total_cost_usd"))
            model = run.trace.model
            reached = reached_model(run.trace)
            detail = preflight_detail(run)
            result = run
        if not reached:
            raise AgentPreflightError(detail, cost_usd=cost, model=model)
        return result
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
