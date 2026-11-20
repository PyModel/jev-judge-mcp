"""Run the offline bench agent against a scripted stdio peer, without a provider or a proxy."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

AGENT = Path(__file__).resolve().parents[1] / "support" / "bench_agent.py"
PEER = """
import json, subprocess, sys
mode = sys.argv[1]
def reply(i):
    print(json.dumps({"jsonrpc": "2.0", "id": i, "result": {"content": [str(i)]}}), flush=True)
for line in sys.stdin:
    msg = json.loads(line)
    if "id" not in msg:
        continue
    i = msg["id"]
    if mode == "inherited":
        subprocess.Popen([sys.executable, "-c", "import time; time.sleep(4)"], stderr=subprocess.DEVNULL)
        reply(99)
        break
    elif i == 0:
        reply(i)
    elif mode == "eof" and i == 2:
        break
    elif mode == "partial_batch":
        if i == 3:
            print(json.dumps({"jsonrpc": "2.0", "method": "notifications/message"}), flush=True)
            reply(2)
            break
    elif mode == "unterminated":
        sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": i, "result": {}}))
        break
    elif mode == "unknown":
        reply(99)
        reply(i)
    elif mode == "duplicate":
        reply(i)
        reply(i)
    elif mode == "reordered":
        if i == 2:
            print(json.dumps({"jsonrpc": "2.0", "method": "notifications/message"}), flush=True)
            reply(2)
            reply(1)
    elif mode == "rpc_error":
        print(json.dumps({"jsonrpc": "2.0", "id": i, "error": {"code": -32603, "message": "error"}}), flush=True)
    else:
        reply(i)
if mode == "unterminated_tail":
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/message"}))
sys.exit(7 if mode == "bad_exit" else 0)
"""


@pytest.mark.parametrize(
    ("mode", "count", "parallel", "error"),
    [
        ("empty", 0, True, None),
        ("eof", 2, True, "missing reply ids [2]"),
        ("partial_batch", 3, True, "missing reply ids [1, 3]"),
        ("unterminated", 1, False, "EOF during a JSON-RPC frame"),
        ("unterminated_tail", 0, False, "EOF during a JSON-RPC frame"),
        ("unknown", 1, False, "unknown reply id"),
        ("unknown", 2, True, "unknown reply id 99"),
        ("inherited", 0, True, "unknown reply id"),
        ("duplicate", 2, True, "duplicate reply id"),
        ("duplicate", 1, False, "duplicate reply id"),
        ("reordered", 2, True, None),
        ("rpc_error", 1, False, "JSON-RPC error"),
        ("rpc_error", 2, True, "JSON-RPC error for request 1"),
        ("bad_exit", 0, False, "exit status 7"),
    ],
)
def test_agent_reply_contract(tmp_path: Path, mode: str, count: int, parallel: bool, error: str | None) -> None:
    plan = tmp_path / "plan.json"
    calls: list[dict[str, object]] = [{"tool": "jev_verify", "arguments": {}} for _ in range(count)]
    plan.write_text(json.dumps({"calls": calls, "parallel": parallel, "answer": "done"}))
    config = tmp_path / "mcp.json"
    server: dict[str, object] = {"command": sys.executable, "args": ["-c", PEER, mode], "env": {}}
    config.write_text(json.dumps({"mcpServers": {"jev": server}}))
    result = subprocess.run(
        [sys.executable, str(AGENT), str(plan), "--mcp-config", str(config)],
        capture_output=True,
        text=True,
        timeout=3,
        check=False,
    )
    if error is not None:
        assert result.returncode != 0 and error in result.stderr, result
        assert not result.stdout
    else:
        assert result.returncode == 0, result.stderr
        if count == 0 and parallel:
            assert "0/0 expected replies" in result.stderr
        events = [json.loads(line) for line in result.stdout.splitlines()]
        assert events[-1]["result"] == "done"
        assert [e["message"]["content"][0]["content"] for e in events if e["type"] == "user"] == [
            [str(i + 1)] for i in range(count)
        ]
