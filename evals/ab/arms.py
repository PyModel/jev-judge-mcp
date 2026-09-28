"""The two arms and the one agent configuration they share.

Both arms run the same agent binary, model, effort, built-in tools, turn and dollar budget, timeout,
harness server and user prompt. B adds this worktree's Python server as MCP server `jev` behind the
recording proxy, and one sentence to the system addendum telling the agent to use the task's Jev tool
for the judgment the task hinges on. Nothing else differs (`tests/evals/test_ab_harness.py` checks).
"""

import shutil
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from evals.agent import RELAY_LOG

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
    """Arm B's one added sentence: use the task's Jev tool for its judgment.

    The ab config exposes the server eager and direct (`lifecycle`/`directTools`/`toolPrefix`, as
    the bench's), so the published tools (`jev_verify`, ...) sit in the model's tool list under
    their own names. The sentence names the tool the agent will actually see; the old gateway
    wording named `jev_jev_verify`, which 30 of 33 recorded with-Jev runs never resolved.
    """
    how = f"call the `{tool}` tool" if agent == "pi" else f"call the `mcp__jev__{tool}` tool"
    return (
        f"This task hinges on a judgment. Before you commit to a decision, {how} with the relevant evidence "
        "from the repository, and use its result."
    )


def addendum(arm: str, agent: str, tool: str) -> str:
    return SYSTEM_ADDENDUM if arm == "A" else f"{SYSTEM_ADDENDUM} {jev_sentence(agent, tool)}"


def jev_command() -> list[str]:
    """This worktree's Python server."""
    return [sys.executable, "-m", "jev_judge_mcp"]


def study_venv(root: Path, wheel: Path) -> Path:
    """Build, once per study, a venv OUTSIDE the repo with this package installed from the wheel.

    The sandbox launcher names this interpreter, so no agent-visible file and no `sys.prefix`
    inside the sandbox can name the repo — the D3 contamination route ran through a launcher that
    did. The repo checkout is an editable install whose `.pth` names `src/`, so copying the repo
    venv would not meet that bar; only a wheel-built venv does. Dependencies come from the wheel's
    metadata (network, at study launch: the operator's paid action).
    """
    venv = root / "jev-study-venv"
    python = venv / "bin" / "python"
    if not python.exists():
        subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True)
        subprocess.run([str(python), "-m", "pip", "install", str(wheel)], check=True)
    return python


def ensure_wheel() -> Path:
    """The built wheel for this checkout, building it if `make build` has not run yet."""
    dist = REPO_ROOT / "dist"
    wheels = sorted(dist.glob("jev_judge_mcp-*.whl"))
    if wheels:
        return wheels[-1]
    subprocess.run(["uv", "build", "--wheel"], cwd=REPO_ROOT, check=True)
    wheels = sorted(dist.glob("jev_judge_mcp-*.whl"))
    if not wheels:
        raise RuntimeError("uv build --wheel produced no wheel")
    return wheels[-1]


def sandbox_python(sandbox: Path, interpreter: str | None = None) -> Path:
    """A sandbox-local interpreter for the servers: `sandbox/bin/python3`, a launcher for the study
    venv's python.

    The agent reads its own MCP config (recorded runs show it did), so the config text must name no
    host path outside the run sandbox. `interpreter` is the per-study venv built from the wheel
    (`study_venv`) — neutral, outside the repo. Empty falls back to this process's interpreter,
    which names the repo and is the offline dry-run shape only.
    """
    bin_dir = sandbox / "bin"
    bin_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    launcher = bin_dir / "python3"
    if not launcher.exists():
        target = interpreter or sys.executable
        launcher.write_text(f'#!/bin/sh\nexec {target!r} "$@"\n', encoding="utf-8")
        launcher.chmod(0o755)
    return launcher


def copy_servers(sandbox: Path, proxy_package: str) -> Path:
    """Copy the relay, the proxy, and the harness server into the sandbox so the agent-visible MCP
    config needs no repo path. `proxy_package` is `ab` or `bench`.
    """
    servers = sandbox / "servers"
    package = servers / "evals" / proxy_package
    package.mkdir(parents=True, exist_ok=True)
    (servers / "evals" / "__init__.py").write_bytes((REPO_ROOT / "evals" / "__init__.py").read_bytes())
    (servers / "evals" / "relay.py").write_bytes((REPO_ROOT / "evals" / "relay.py").read_bytes())
    (package / "__init__.py").write_bytes((REPO_ROOT / "evals" / proxy_package / "__init__.py").read_bytes())
    (package / "proxy.py").write_bytes((REPO_ROOT / "evals" / proxy_package / "proxy.py").read_bytes())
    shutil.copyfile(REPO_ROOT / "evals" / "ab" / "review_server.py", servers / "harness_server.py")
    return servers


def keyfile_env(sandbox: Path, api_key: str) -> dict[str, str]:
    """Write the TypeSafe key as a 0600 file inside the sandbox and return the env that points the
    server at it.

    The key must never sit in the MCP config the agent can read: in the D3 study the agents read
    that config's env and echoed the key to the model provider in 29 of 33 with-Jev runs. The
    server reads `JEV_MCP_KEY_FILE` (ADR-0046). The file is still inside the sandbox the agent can
    read; only the confinement boundary fully hides it.
    """
    path = sandbox / "typesafe.key"
    path.write_text(api_key + "\n", encoding="utf-8")
    path.chmod(0o600)
    return {"JEV_MCP_KEY_FILE": str(path)}


def mcp_config(
    arm: str, *, sandbox: Path, server_env: Mapping[str, str], api_key: str = "", interpreter: str | None = None
) -> dict[str, Any]:
    """The `--mcp-config` document, built against the run's private sandbox.

    Every path in the document is inside `sandbox`: the servers run from copies placed there, the
    interpreter is the sandbox symlink, and the relay log is written there and copied out to the
    run's records after the run. Only B carries `server_env`.
    """
    python = sandbox_python(sandbox, interpreter)
    servers = copy_servers(sandbox, "ab")
    harness = {
        "type": "stdio",
        "command": str(python),
        "args": [str(servers / "harness_server.py")],
    }
    servers_doc: dict[str, Any] = {"harness": harness}
    if arm != "A":
        env = {**dict(server_env), "PYTHONPATH": str(servers)}
        if api_key:
            env = {k: v for k, v in env.items() if k != "TYPESAFE_API_KEY"}
            env.update(keyfile_env(sandbox, api_key))
        servers_doc["jev"] = {
            "type": "stdio",
            "command": str(python),
            "args": [
                "-m",
                "evals.ab.proxy",
                str(sandbox / RELAY_LOG),
                "--",
                str(python),
                "-m",
                "jev_judge_mcp",
            ],
            "env": env,
            "lifecycle": "eager",
            "directTools": True,
            "toolPrefix": "none",
        }
    return {"mcpServers": servers_doc}


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
