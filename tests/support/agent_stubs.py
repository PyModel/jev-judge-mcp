"""Stand-in agent transcripts for the offline preflight tests. No model and no credential.

Claude and Pi do not share a stream shape, so the reaching stub is per agent. Both report a billed
turn and a cost of 0.01, which is what a preflight records.
"""


def dead_login() -> str:
    """Exits 1 after writing the startup line Claude Code prints when it cannot find its login."""
    return "\n".join(
        [
            "import sys",
            'sys.stderr.write("Not logged in · Please run /login\\n")',
            "raise SystemExit(1)",
        ]
    )


def reaches_model(agent: str) -> str:
    """One billed assistant turn. `agent` is `claude` or `pi`."""
    if agent == "pi":
        return "\n".join(
            [
                "import json",
                "events = [",
                '    {"type": "message_end", "message": {"role": "assistant", "provider": "stub",',
                '        "model": "stub-model", "usage": {"input": 5, "output": 3, "cacheRead": 0,',
                '        "cacheWrite": 0, "totalTokens": 8, "cost": {"total": 0.01}},',
                '        "content": [{"type": "text", "text": "ok"}], "stopReason": "stop"}},',
                '    {"type": "agent_end", "willRetry": False},',
                "]",
                "for event in events:",
                "    print(json.dumps(event))",
            ]
        )
    return "\n".join(
        [
            "import json",
            'print(json.dumps({"type": "system", "subtype": "init", "model": "stub-model", "mcp_servers": []}))',
            'print(json.dumps({"type": "assistant", "message": {"id": "m",',
            '    "usage": {"input_tokens": 5, "output_tokens": 3},',
            '    "content": [{"type": "text", "text": "ok"}]}}))',
            'print(json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": "ok",',
            '    "total_cost_usd": 0.01}))',
        ]
    )
