"""The two arms and the one agent configuration they share.

Both arms run the same agent binary, model, effort, built-in tools, turn and dollar budget, timeout,
harness server and user prompt. B adds this worktree's Python server as MCP server `jev` behind the
recording proxy, and one sentence to the system addendum telling the agent to use the task's Jev tool
for the judgment the task hinges on. Nothing else differs (`tests/evals/test_ab_harness.py` checks).
"""

import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

ARMS = ("A", "B")
ARM_LABELS = {"A": "without Jev", "B": "with Jev MCP"}

AGENT_MODEL = "claude-sonnet-5"
AGENT_EFFORT = "medium"
AGENT_TEMPERATURE = "not settable through the Claude Code CLI; its default, identical in every arm"
BUILTIN_TOOLS = ("Bash", "Read", "Edit", "Write", "Glob", "Grep")
MAX_TURNS = 40
RUN_BUDGET_USD = 2.00
"""Passed to `claude --max-budget-usd`; the CLI can overshoot by the call in flight."""
RUN_TIMEOUT_S = 900

JEV_MODEL = "jev-1.13.0"

SYSTEM_ADDENDUM = (
    "You are working in a small Python repository. Complete the task in the user's message, run the test "
    "suite to check your work, and stop when you are done. Use whichever of your available tools help."
)
REPO_ROOT = Path(__file__).resolve().parents[2]


def jev_sentence(agent: str, tool: str) -> str:
    """Arm B's one added sentence: use the task's Jev tool for its judgment. Pi reaches the server
    through its `mcp` gateway tool, which exposes each server tool as `<server>_<tool>` (`jev_jev_verify`)."""
    how = (
        f"connect to the jev MCP server through the mcp gateway tool and call its `jev_{tool}` tool"
        if agent == "pi"
        else f"call the `mcp__jev__{tool}` tool"
    )
    return (
        f"This task hinges on a judgment. Before you commit to a decision, {how} with the relevant evidence "
        "from the repository, and use its result."
    )


def addendum(arm: str, agent: str, tool: str) -> str:
    return SYSTEM_ADDENDUM if arm == "A" else f"{SYSTEM_ADDENDUM} {jev_sentence(agent, tool)}"


def jev_command() -> list[str]:
    """This worktree's Python server."""
    return [sys.executable, "-m", "jev_judge_mcp"]


def mcp_config(arm: str, *, jev_log: Path, server_env: Mapping[str, str]) -> dict[str, Any]:
    """The `--mcp-config` document. Only B carries `server_env` (the TypeSafe key, live: `jev_env`)."""
    servers: dict[str, Any] = {
        "harness": {
            "type": "stdio",
            "command": sys.executable,
            "args": ["-m", "evals.ab.review_server"],
            "env": {"PYTHONPATH": str(REPO_ROOT)},
        },
    }
    if arm != "A":
        servers["jev"] = {
            "type": "stdio",
            "command": sys.executable,
            "args": ["-m", "evals.ab.proxy", str(jev_log), "--", *jev_command()],
            "env": dict(server_env),
        }
    return {"mcpServers": servers}


def jev_env(*, api_key: str, path: str) -> dict[str, str]:
    return {
        "JEV_PROVIDER": "typesafe",
        "TYPESAFE_API_KEY": api_key,
        "JEV_MCP_MODEL": JEV_MODEL,
        "PYTHONPATH": str(REPO_ROOT),
        "PATH": path,
    }


def claude_command(claude: str, prompt: str, mcp_config_path: Path, addendum: str = SYSTEM_ADDENDUM) -> list[str]:
    return [
        claude,
        "--print",
        prompt,
        "--output-format",
        "stream-json",
        "--verbose",
        "--model",
        AGENT_MODEL,
        "--effort",
        AGENT_EFFORT,
        "--setting-sources",
        "",
        "--strict-mcp-config",
        "--mcp-config",
        str(mcp_config_path),
        "--tools",
        ",".join(BUILTIN_TOOLS),
        "--allowedTools",
        *BUILTIN_TOOLS,
        "mcp__harness",
        "mcp__jev",
        "--append-system-prompt",
        addendum,
        "--max-turns",
        str(MAX_TURNS),
        "--max-budget-usd",
        f"{RUN_BUDGET_USD:.2f}",
        "--no-session-persistence",
    ]


def agent_env(environ: Mapping[str, str]) -> dict[str, str]:
    """A minimal env for the agent: no provider keys, no virtualenv, nothing from this repo's shell."""
    venv = environ.get("VIRTUAL_ENV")
    path = ":".join(p for p in environ.get("PATH", "").split(":") if p and not (venv and p.startswith(venv)))
    env = {key: environ[key] for key in ("HOME", "USER", "LOGNAME", "SHELL", "LANG", "TMPDIR") if key in environ}
    return {**env, "PATH": path, "TERM": "dumb"}
