"""A stub for `claude --print ... --output-format stream-json`: `python bench_agent.py PLAN [claude args...]`.

Stdlib only. It reads `--mcp-config`, spawns the `jev` server there (the bench proxy in front of the
real Jev server), runs the MCP handshake, makes the tool calls PLAN lists, and prints stream-json
events shaped like the recorded sample (`tests/evals/data/claude-stream-json-sample.jsonl`): an init
event, one assistant `tool_use` and one user `tool_result` per call, the final text, and a result.

PLAN is JSON: `calls` ([{tool, arguments}]), `answer` (the final text), and optional `parallel`
(send every call before reading any reply), `hide` (tool names left out of the stream, to fake a
harness mismatch), `subtype` (default `success`), and, for the outcome study's coding tasks, `files`
({path: text} written into the working directory) and `uses` ([{name, input}] built-in tool uses
emitted after the Jev calls, each with a successful result).
"""

import json
import subprocess
import sys
from typing import IO, Any, cast


def _send(pipe: IO[bytes], message: dict[str, Any]) -> None:
    pipe.write((json.dumps(message) + "\n").encode())
    pipe.flush()


def _reply(pipe: IO[bytes], wanted: int) -> dict[str, Any]:
    for line in iter(pipe.readline, b""):
        message = json.loads(line)
        if message.get("id") == wanted:
            return message
    raise RuntimeError(f"server closed before replying to {wanted}")


def _emit(event: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(event) + "\n")


def main(argv: list[str]) -> int:
    plan: dict[str, Any] = json.loads(open(argv[0], encoding="utf-8").read())
    args = argv[1:]
    config = json.loads(open(args[args.index("--mcp-config") + 1], encoding="utf-8").read())
    server = config["mcpServers"].get("jev")
    calls: list[dict[str, Any]] = plan.get("calls", []) if server else []
    replies: list[dict[str, Any]] = []
    servers: list[dict[str, str]] = []
    if server:
        child = subprocess.Popen(
            [server["command"], *server["args"]], stdin=subprocess.PIPE, stdout=subprocess.PIPE, env=server["env"]
        )
        assert child.stdin is not None and child.stdout is not None
        init = {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "bench-stub", "version": "0"},
        }
        _send(child.stdin, {"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": init})
        _reply(child.stdout, 0)
        _send(child.stdin, {"jsonrpc": "2.0", "method": "notifications/initialized"})
        servers.append({"name": "jev", "status": "connected"})
        requests: list[dict[str, Any]] = [
            {
                "jsonrpc": "2.0",
                "id": n + 1,
                "method": "tools/call",
                "params": {"name": c["tool"], "arguments": c["arguments"]},
            }
            for n, c in enumerate(calls)
        ]
        if plan.get("parallel"):
            for request in requests:
                _send(child.stdin, request)
            by_id: dict[object, dict[str, Any]] = {}
            for line in iter(child.stdout.readline, b""):
                message = cast(dict[str, Any], json.loads(line))
                by_id[message.get("id")] = message
                if len(by_id) == len(requests):
                    break
            replies = [by_id[q["id"]] for q in requests]
        else:
            for request in requests:
                _send(child.stdin, request)
                replies.append(_reply(child.stdout, request["id"]))
        child.stdin.close()
        child.wait(timeout=30)
    _emit({"type": "system", "subtype": "init", "model": "claude-sonnet-5", "tools": [], "mcp_servers": servers})
    hidden = set(plan.get("hide", []))
    for n, (call, reply) in enumerate(zip(calls, replies, strict=True)):
        if call["tool"] in hidden:
            continue
        use_id = f"toolu_stub_{n}"
        name = f"mcp__jev__{call['tool']}"
        use = {"type": "tool_use", "id": use_id, "name": name, "input": call["arguments"]}
        _emit({"type": "assistant", "message": {"id": f"msg_{n}", "usage": {"input_tokens": 10}, "content": [use]}})
        result = cast(dict[str, Any], reply.get("result") or {})
        block = {
            "type": "tool_result",
            "tool_use_id": use_id,
            "is_error": bool(result.get("isError")),
            "content": result.get("content"),
        }
        _emit({"type": "user", "message": {"role": "user", "content": [block]}})
    for rel, content in cast(dict[str, str], plan.get("files", {})).items():
        with open(rel, "w", encoding="utf-8") as sink:
            sink.write(content)
    for n, extra in enumerate(cast(list[dict[str, Any]], plan.get("uses", []))):
        use_id = f"toolu_stub_use_{n}"
        use = {"type": "tool_use", "id": use_id, "name": extra["name"], "input": extra["input"]}
        _emit({"type": "assistant", "message": {"id": f"msg_use_{n}", "usage": {"input_tokens": 10}, "content": [use]}})
        block = {"type": "tool_result", "tool_use_id": use_id, "is_error": False, "content": "ok"}
        _emit({"type": "user", "message": {"role": "user", "content": [block]}})
    text = str(plan["answer"])
    _emit(
        {
            "type": "assistant",
            "message": {
                "id": "msg_final",
                "usage": {"input_tokens": 10, "output_tokens": 5},
                "content": [{"type": "text", "text": text}],
            },
        }
    )
    subtype = plan.get("subtype", "success")
    _emit(
        {
            "type": "result",
            "subtype": subtype,
            "is_error": subtype != "success",
            "result": text,
            "total_cost_usd": 0.0,
            "num_turns": len(calls) + 1,
            "duration_ms": 1,
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
