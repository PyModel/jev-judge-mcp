---
status: accepted
---
# The server is POSIX-only until Windows is implemented and tested

The stdio server stops on SIGINT and SIGTERM through `anyio.open_signal_receiver`, which needs `loop.add_signal_handler`. That call is Unix-only. The extract worker arms `signal.SIGALRM` and `setitimer` so an orphaned match still ends (`extract/worker.py`). Neither exists on Windows. The Node reference runs on any operating system through `npx`. Nothing declared a platform scope, so a Windows host fails at startup inside the signal task, or later inside every extract worker.

Windows is unsupported until Windows-specific behavior is implemented and tested. `pyproject.toml` carries the `Operating System :: POSIX` classifier. On a non-POSIX platform (`sys.platform` starting with `win`) `main` raises `SystemExit` with one message that names that platform, before settings, the server, signal handlers, or the extract worker pool.

## Consequences

- A startup test patches `sys.platform` to `win32` and expects that exit, and that the server is not built.
- This is a Sanctioned Divergence from the reference (`posix-only`): the reference process starts on Windows. This one does not.
- Making the server portable (signal handling only where `add_signal_handler` exists, no `SIGALRM` on Windows, a Windows CI job) replaces this decision. It is not this change.
