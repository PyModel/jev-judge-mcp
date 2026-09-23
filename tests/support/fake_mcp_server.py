"""A stand-in MCP server for the recording relays: `python tests/support/fake_mcp_server.py`.

Reads newline-delimited JSON-RPC and answers each `tools/call` by its tool name:

- `jev_screen`: a text result that is not JSON;
- `fake_error`: a JSON-RPC error (`FAKE_ERROR`), no result;
- `fake_collide`: first a server-to-client request that reuses the call's id, then the normal answer;
- `fake_exit`: no answer; the server exits at once;
- `fake_hang`: no answer; the server keeps reading;
- any other name: a JSON body with the tool's name, `model`, `action`, `usage` and a `secret_arg` field.

Every `tools/call` it receives, with or without an id, alone or inside a batch array, is counted on
stderr as one `received tools/call` line, so a test can prove what the relay kept from the server.
Other messages with an id get an empty result; notifications get nothing.
"""

import json
import sys
from typing import Any, cast

FAKE_BODY: dict[str, Any] = {
    "action": "review",
    "model": "jev-1.13.0",
    "usage": {"input_tokens": 321},
    "secret_arg": "x",
}
FAKE_ERROR: dict[str, Any] = {"code": -32603, "message": "fake server error"}
RECEIVED = "received tools/call"


def _send(message: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(message) + "\n")
    sys.stdout.flush()


def _answer(message: dict[str, Any]) -> None:
    if message.get("method") == "tools/call":
        sys.stderr.write(RECEIVED + "\n")
        sys.stderr.flush()
    if "id" not in message:
        return
    reply_id = message["id"]
    if message.get("method") != "tools/call":
        _send({"jsonrpc": "2.0", "id": reply_id, "result": {}})
        return
    name = message.get("params", {}).get("name") if isinstance(message.get("params"), dict) else None
    if name is None:
        # Positional params (JSON-RPC permits an array) or none at all: no name to dispatch on.
        _send({"jsonrpc": "2.0", "id": reply_id, "error": {"code": -32602, "message": "params must be an object"}})
        return
    if name == "fake_exit":
        raise SystemExit(0)
    if name == "fake_hang":
        return
    if name == "fake_error":
        _send({"jsonrpc": "2.0", "id": reply_id, "error": FAKE_ERROR})
        return
    if name == "fake_collide":
        _send({"jsonrpc": "2.0", "id": reply_id, "method": "sampling/createMessage", "params": {}})
    text = "not json" if name == "jev_screen" else json.dumps({"tool": name, **FAKE_BODY})
    _send({"jsonrpc": "2.0", "id": reply_id, "result": {"content": [{"type": "text", "text": text}]}})


def main() -> None:
    for line in sys.stdin:
        value: Any = json.loads(line)
        messages = cast(list[dict[str, Any]], value if isinstance(value, list) else [value])
        for message in messages:
            _answer(message)


if __name__ == "__main__":
    main()
