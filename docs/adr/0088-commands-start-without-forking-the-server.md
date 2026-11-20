# ADR-0088 — Commands start without forking the server

Status: Accepted  
Date: 2026-11-20

## Context

Every Git read and every `jev_ask` command went through `process.capture`, which started the
command with `subprocess.Popen(cwd=..., start_new_session=True, close_fds=True)` and then polled
`Popen.wait(timeout=0.05)`. A profile on macOS (Python 3.12.14) found two costs:

- `cwd`, `start_new_session` and `close_fds` rule out CPython's `posix_spawn` path, so `Popen`
  forks. The fork holds the GIL for about 1.3 ms, and every other call on the server stalls with
  it. `jev_gate_range` runs Git about 8 times per call, so at 16 concurrent calls the other tools'
  p95 was 53-67 ms against a 20 ms budget (three `make load` runs at load averages 13-26). On Linux `Popen` uses `vfork` and releases the GIL
  during it, so the stall is a macOS cost.
- The polling wait slept about 7 ms per run and noticed the exit up to one interval late.

`os.posix_spawn(setsid=True)` removes the stall, but it cannot close the descriptors the child
inherits. Python creates descriptors non-inheritable, yet on macOS a socket or pipe is inheritable
for an instant after it is created. In a stress test where other threads created sockets and pipes,
1,183 of 1,500 children started with `os.posix_spawn` received a stray descriptor. None of 1,500
started with `Popen(close_fds=True)` did.

## Decision

`process.capture` is the one way the server runs a command. Every command runs in the server's
current directory, in its own session, with stdin at `/dev/null`, its stdout and stderr on two pipes,
and no other descriptor. There is no `cwd` parameter: Git selects its directory with `-C`.

- **macOS** calls libc's `posix_spawn` through ctypes (`darwin_spawn.py`) with
  `POSIX_SPAWN_CLOEXEC_DEFAULT`, which closes every descriptor the file actions do not name,
  `POSIX_SPAWN_SETSID`, default handlers for `SIGPIPE` and `SIGXFSZ` (what `Popen` restores), and an
  empty signal mask. ctypes releases the GIL for the call. A bare program name resolves against the
  child's `PATH`, as `Popen` does. In the same stress test, 0 of 1,500 children received a stray
  descriptor.
- **Linux** keeps `Popen(start_new_session=True)` with `close_fds`, which already uses `vfork`
  without the GIL.

The calling thread waits on one selector holding both pipes and a descriptor that becomes readable
when the command exits. On macOS that is a kqueue `NOTE_EXIT` filter. On Linux it is a pipe that a
watcher thread closes once `os.waitid(..., WEXITED | WNOWAIT)` returns. `os.pidfd_open` would need no
thread, but python-build-standalone, the Python uv installs, lacks it. A run ends when the command
exits, not when its pipes close, so a background child that keeps the pipes open cannot stretch a
successful run. The process group is killed before the
command is reaped, so its id cannot have passed to another process. Then the pipes drain for at
most `DRAIN_GRACE_SECONDS`; output still open after that is an error. A timeout raises
`subprocess.TimeoutExpired` and output past the byte cap raises `OutputTooLarge`. Each first kills
the group, without waiting out the drain.

Rejected:

- `os.posix_spawn`: it leaks descriptors under concurrency, as measured above.
- A fallback launcher for a platform without these calls: the server supports macOS and Linux
  (ADR-0032), and an untested second path would decide what a command can reach.
- Fewer Git runs per `jev_gate_range` call: a separate change to that tool's reads.

## Consequences

- The runner depends on a ctypes binding to libc's `posix_spawn` and eleven helpers that set up its
  attributes, file actions and signal sets. Their signatures and the four flag values come from the
  macOS SDK's `spawn.h`, `sys/spawn.h` and `signal.h`; `tests/unit/test_process.py` pins the
  behaviour they must produce.
- A Git directory that does not exist is Git's own failure (exit 128), not an `OSError` at launch.
  Every caller already treats a nonzero exit as failure; `range_has_changes` reports it as `None`,
  so the push-range lookup still ends in `GitDiffError`. A relative directory, which the old launch
  applied twice (as the child's working directory, then again through `-C`), is now applied once.
- Each Linux run starts one watcher thread, in place of the two reader threads it had.
- Tests that must see a launch without running it patch `process._launch`, the launch point on
  every platform.
