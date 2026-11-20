"""Failure handling for the shared subprocess harness (tests/support/stdio.py), run by `make eval`."""

import subprocess
import sys
from pathlib import Path

import pytest

from tests.support.stdio import StdioServer

_TRACEBACK = "late child diagnostic"
_FLOOD = "import os, sys; os.write(2, b'x' * (1024 * 1024) + b'\\n'); "


@pytest.mark.parametrize("path", ["readiness-eof", "readiness-timeout", "exit", "timeout", "inherited"])
def test_a_failed_child_is_reaped_and_reports_stderr(path: str, tmp_path: Path) -> None:
    inherited_closed = tmp_path / "closed"
    script = _FLOOD
    if path in {"readiness-timeout", "timeout"}:
        script += (
            "print('late ' + 'child diagnostic', file=sys.stderr, flush=True); "
            "print('{}', flush=True); sys.stdin.read()"
        )
        error = TimeoutError if path == "readiness-timeout" else subprocess.TimeoutExpired
    else:
        if path in {"exit", "inherited"}:
            script += "os.write(1, b'y' * (1024 * 1024)); "
        if path == "inherited":
            peer = (
                "import select, sys; from pathlib import Path; "
                "select.select([sys.stdin], [], [], 30); Path(sys.argv[1]).touch()"
            )
            script += f"import subprocess; subprocess.Popen([sys.executable, '-c', {peer!r}, sys.argv[1]]); "
        script += f"raise RuntimeError({_TRACEBACK!r})"
        error = EOFError if path == "readiness-eof" else AssertionError

    child = StdioServer([sys.executable, "-c", script, str(inherited_closed)])
    with pytest.raises(error, match=_TRACEBACK), child:
        if path == "readiness-eof":
            child.receive(timeout=5)
        elif path == "readiness-timeout":
            assert child.read_line(timeout=5) == b"{}\n"
            child.read_line(timeout=0.2)
        elif path == "timeout":
            assert child.receive(timeout=5) == {}
            child.wait(timeout=0.2)
        else:
            returncode, _ = child.wait(timeout=5)
            assert not inherited_closed.exists()
            assert returncode == 0
    assert child.process.returncode is not None


def test_request_deadline_includes_notifications() -> None:
    script = """
import json, sys, time
sys.stdin.readline()
note = json.dumps({"jsonrpc": "2.0", "method": "notifications/message"})
for _ in range(30):
    print(note, flush=True)
    time.sleep(0.01)
print(json.dumps({"jsonrpc": "2.0", "id": 1, "result": {}}), flush=True)
"""
    with StdioServer([sys.executable, "-c", script]) as server, pytest.raises(TimeoutError, match="deadline"):
        server.request({"jsonrpc": "2.0", "id": 1, "method": "ping"}, timeout=0.15)
