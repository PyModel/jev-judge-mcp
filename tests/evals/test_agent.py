"""The shared agent runner offline: env boundary, config, timeout, cleanup, and scrubbing, plus the outcome
study and the bench driving it with stand-in agents. Nothing here calls a model or a provider.
"""

import json
import os
import signal
import stat
import subprocess
import sys
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from evals.ab import arms, ledger, report, tasks
from evals.ab import run as ab_run
from evals.ab.stream import Trace
from evals.agent import STDERR_TRUNCATED_LINE, AgentCommand, AgentRunResult, run_agent, secret_scrub
from evals.bench import ledger as bench_ledger
from evals.bench import run
from evals.bench.items import load_items
from evals.spend import SpendLedger, SpendPolicy

KEY = "sekrit-123"
REPO = Path(__file__).resolve().parents[2]
BENCH_AGENT = REPO / "tests" / "support" / "bench_agent.py"

# A stand-in agent: prints its env and argv as a successful stream-json result, the secret included,
# then sleeps for argv[1] seconds.
STUB = """
import json, os, sys, time
print(json.dumps({"type": "system", "subtype": "init", "model": "stub", "mcp_servers": []}), flush=True)
print("leaked " + os.environ.get("STUB_SECRET", ""), flush=True)
time.sleep(float(sys.argv[1]))
result = {"env": dict(os.environ), "argv": sys.argv[2:], "cwd": os.getcwd()}
print(json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": json.dumps(result)}))
"""

# Forks a grandchild in the agent's process group, records both pids, and sleeps. SIGHUP is ignored so
# only a group SIGKILL reaps the grandchild; the agent's death alone must not.
GROUP = """
import os, signal, time
from pathlib import Path
signal.signal(signal.SIGHUP, signal.SIG_IGN)
child = os.fork()
if child == 0:
    Path("child.pid").write_text(str(os.getpid()))
    time.sleep(300)
    raise SystemExit(0)
Path("agent.pid").write_text(str(os.getpid()))
Path("agent.pgid").write_text(str(os.getpgid(0)))
deadline = time.time() + 5
while not Path("child.pid").exists():
    if time.time() > deadline:
        raise SystemExit("child pid was not written")
    time.sleep(0.01)
time.sleep(300)
"""


def _stub(sleep_s: float = 0.0, *, timeout_s: float = 30, env: dict[str, str] | None = None) -> AgentCommand:
    return AgentCommand(
        argv=lambda config: [sys.executable, "-c", STUB, str(sleep_s), "--mcp-config", str(config)],
        timeout_s=timeout_s,
        env={"STUB_SECRET": KEY, **(env or {})},
    )


def _kept(run_dir: Path) -> str:
    return "".join(p.read_text(encoding="utf-8") for p in run_dir.rglob("*") if p.is_file())


def test_env_is_base_env_then_command_env_and_never_ambient(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AMBIENT_ONLY", "leak")
    base = {"PATH": "/usr/bin:/bin", "KEEP": "base", "OVERRIDE": "base"}
    with run_agent(
        _stub(env={"OVERRIDE": "command"}), mcp_config={}, base_env=base, secret=KEY, run_dir=tmp_path
    ) as agent:
        seen = json.loads(agent.trace.result_field("result"))
    env = seen["env"]
    assert "AMBIENT_ONLY" not in env
    assert (env["KEEP"], env["OVERRIDE"], env["PATH"]) == ("base", "command", "/usr/bin:/bin")


def test_the_agent_never_sees_the_real_home_or_the_repo(tmp_path: Path) -> None:
    """HOME, the pi agent dir, and TMPDIR are private; only the allowlist crosses.

    The sandbox gap this pins: a recorded smoke run's agent read `~/.pi/agent/mcp.json` and
    `~/.pi/agent/.env` and ran `uv sync` in a real checkout. The env the agent sees must never
    point at the caller's home tree or this repository, and the private agent dir must hold
    credentials and the model catalog only — never the user's MCP config.
    """
    real_home = tmp_path / "real-home"
    real_agent = real_home / ".pi" / "agent"
    real_agent.mkdir(parents=True)
    (real_agent / "auth.json").write_text('{"deepseek": {"type": "api_key", "key": "k"}}', encoding="utf-8")
    (real_agent / "mcp.json").write_text('{"mcpServers": {"user": {"command": "x"}}}', encoding="utf-8")
    (real_home / ".env").write_text("SOMETHING=x\n", encoding="utf-8")
    base = {
        "HOME": str(real_home),
        "USER": "tester",
        "LOGNAME": "tester",
        "SHELL": "/bin/zsh",
        "LANG": "C",
        "TMPDIR": str(tmp_path / "outer-tmp"),
        "PATH": "/usr/bin:/bin",
    }
    probe = (
        "import json, os\n"
        'result = {"env": dict(os.environ),\n'
        '          "agent_dir": sorted(os.listdir(os.environ["PI_CODING_AGENT_DIR"])),\n'
        '          "home": sorted(os.listdir(os.environ["HOME"]))}\n'
        'init = json.dumps({"type": "system", "subtype": "init", "model": "stub", "mcp_servers": []})\n'
        'line = json.dumps({"type": "result", "subtype": "success", "is_error": False,\n'
        '                  "result": json.dumps(result)})\n'
        "print(init, flush=True)\n"
        "print(line)\n"
    )
    command = AgentCommand(argv=lambda _config: [sys.executable, "-c", probe], timeout_s=30, env={"STUB_SECRET": KEY})
    run_dir = tmp_path / "records"
    with run_agent(command, mcp_config={}, base_env=base, secret=KEY, run_dir=run_dir) as agent:
        seen = json.loads(agent.trace.result_field("result"))
    env = seen["env"]
    # The allowlist crosses; the user's MCP config, env files, and everything else do not.
    assert seen["agent_dir"] == ["auth.json"]
    assert seen["home"] == []
    assert env["PI_CODING_AGENT_DIR"] != str(real_agent)
    for name, value in env.items():
        assert str(real_home) not in value, f"{name} points at the real home"
        assert str(REPO) not in value, f"{name} points at the repo checkout"
    assert env["HOME"] != str(real_home)
    assert env["TMPDIR"] != str(tmp_path / "outer-tmp")


def test_config_is_private_argv_carries_its_path_and_the_run_succeeds(tmp_path: Path) -> None:
    modes: list[int] = []

    def argv(config: Path) -> Sequence[str]:
        modes.append(stat.S_IMODE(config.stat().st_mode))
        assert json.loads(config.read_text(encoding="utf-8")) == {"mcpServers": {"x": {"env": {"K": KEY}}}}
        return _stub().argv(config)

    command = AgentCommand(argv=argv, timeout_s=30, env={"STUB_SECRET": KEY})
    config = {"mcpServers": {"x": {"env": {"K": KEY}}}}
    with run_agent(command, mcp_config=config, base_env={}, secret=KEY, run_dir=tmp_path) as agent:
        assert agent.workdir.is_dir()
        config_path = Path(agent.argv[agent.argv.index("--mcp-config") + 1])
        assert not config_path.exists(), "the config is deleted as soon as the agent exits"
        seen = json.loads(agent.trace.result_field("result"))
        assert Path(seen["cwd"]).resolve() == agent.workdir
    assert modes == [0o600]
    assert (agent.status, agent.returncode) == ("ok", 0)
    assert not agent.workdir.exists() and not config_path.parent.exists()
    assert KEY not in _kept(tmp_path) and "leaked [REDACTED]" in _kept(tmp_path)


def test_timeout_has_no_return_code_and_is_scrubbed(tmp_path: Path) -> None:
    with run_agent(_stub(30, timeout_s=1), mcp_config={}, base_env={}, secret=KEY, run_dir=tmp_path) as agent:
        assert (agent.status, agent.returncode) == ("failed: timeout after 1s", None)
    assert "leaked [REDACTED]" in (tmp_path / "stream.jsonl").read_text(encoding="utf-8")
    assert KEY not in _kept(tmp_path) and not agent.workdir.exists()


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _gone(pid: int) -> bool:
    """Dead, or a zombie that has not been reaped. A zombie is not running."""
    if not _alive(pid):
        return True
    stat_text = subprocess.run(
        ["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True, check=False
    ).stdout.strip()
    return stat_text == "" or stat_text.startswith("Z")


def _wait_gone(pid: int) -> None:
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        if _gone(pid):
            return
        time.sleep(0.05)
    listing = subprocess.run(
        ["ps", "-A", "-o", "pid=,ppid=,stat=,command="], capture_output=True, text=True, check=False
    ).stdout
    assert _gone(pid), f"pid {pid} still running\n{listing}"


def _group_gone(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return True
    return False


def _wait_group_gone(pgid: int) -> None:
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        if _group_gone(pgid):
            return
        time.sleep(0.05)
    assert _group_gone(pgid), f"process group {pgid} still exists"


def test_timeout_kills_the_agent_group_and_leaves_the_study_alive(tmp_path: Path) -> None:
    study, study_pgid = os.getpid(), os.getpgid(0)
    command = AgentCommand(argv=lambda _config: [sys.executable, "-c", GROUP], timeout_s=2, env={"STUB_SECRET": KEY})
    agent_pid = child_pid = agent_pgid = 0
    try:
        with run_agent(command, mcp_config={}, base_env={}, secret=KEY, run_dir=tmp_path) as agent:
            agent_pid = int((agent.workdir / "agent.pid").read_text(encoding="utf-8"))
            child_pid = int((agent.workdir / "child.pid").read_text(encoding="utf-8"))
            agent_pgid = int((agent.workdir / "agent.pgid").read_text(encoding="utf-8"))
            assert agent.returncode is None and agent.status == "failed: timeout after 2s"
            assert agent_pid != study and child_pid != study
        assert study == os.getpid() and _alive(study)
        assert agent_pgid != study_pgid
        _wait_gone(agent_pid)
        _wait_gone(child_pid)
        _wait_group_gone(agent_pgid)
    finally:
        if agent_pgid > 0 and not _group_gone(agent_pgid):
            try:
                os.killpg(agent_pgid, signal.SIGKILL)
            except ProcessLookupError:
                pass


def test_stdout_over_the_cap_scrubs_and_raises_a_bookable_exception(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("evals.agent.STDOUT_CAP_BYTES", 1024)
    flood = """
import os, sys, time
print("leaked " + os.environ.get("STUB_SECRET", ""), flush=True)
sys.stdout.buffer.write(b"x" * 100000)
sys.stdout.buffer.flush()
time.sleep(300)
"""
    command = AgentCommand(argv=lambda _config: [sys.executable, "-c", flood], timeout_s=30, env={"STUB_SECRET": KEY})
    policy = SpendPolicy(max_runs=2, max_usd=10, jev_usd_per_mtok_input=0, run_bound_usd=1.5)
    book = SpendLedger(policy, tmp_path / "ledger.json")
    started = time.monotonic()
    with pytest.raises(OSError, match="stdout exceeded"):
        try:
            with run_agent(command, mcp_config={}, base_env={}, secret=KEY, run_dir=tmp_path) as agent:
                raise AssertionError(f"capped run must not yield, got {agent.status}")
        except Exception:
            book.record_unfinished("cap")
            raise
    assert time.monotonic() - started < 10, "the cap must kill the agent without waiting out the timeout"
    assert book.runs["cap"] == policy.run_bound_usd
    assert "leaked [REDACTED]" in (tmp_path / "stream.jsonl").read_text(encoding="utf-8")
    assert KEY not in _kept(tmp_path)


def test_stderr_past_the_cap_is_dropped_and_the_run_still_finishes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("evals.agent.STDOUT_CAP_BYTES", 1024)
    flood = """
import json, os, sys
print(json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": "ok"}), flush=True)
sys.stderr.buffer.write(("leaked " + os.environ.get("STUB_SECRET", "") + "\\n").encode())
sys.stderr.buffer.write(b"y" * 200000)
sys.stderr.buffer.flush()
"""
    command = AgentCommand(argv=lambda _config: [sys.executable, "-c", flood], timeout_s=30, env={"STUB_SECRET": KEY})
    started = time.monotonic()
    with run_agent(command, mcp_config={}, base_env={}, secret=KEY, run_dir=tmp_path) as agent:
        assert (agent.status, agent.returncode) == ("ok", 0)
        assert agent.stderr.endswith(f"{STDERR_TRUNCATED_LINE}\n")
        assert agent.stderr.count(STDERR_TRUNCATED_LINE) == 1
        assert "y" * 2000 not in agent.stderr
        assert f"leaked {KEY}" in agent.stderr
    assert time.monotonic() - started < 10, "dropping stderr must keep draining so the agent cannot block"
    logged = (tmp_path / "claude.stderr").read_text(encoding="utf-8")
    assert logged.endswith(f"{STDERR_TRUNCATED_LINE}\n") and logged.count(STDERR_TRUNCATED_LINE) == 1
    assert "leaked [REDACTED]" in logged and KEY not in _kept(tmp_path)


def test_a_failure_inside_the_block_still_cleans_up_and_scrubs(tmp_path: Path) -> None:
    workdirs: list[Path] = []
    with (
        pytest.raises(RuntimeError, match="grading broke"),
        run_agent(_stub(), mcp_config={}, base_env={}, secret=KEY, run_dir=tmp_path) as agent,
    ):
        workdirs.append(agent.workdir)
        (tmp_path / "result.json").write_text(f'{{"key": "{KEY}"}}', encoding="utf-8")
        raise RuntimeError("grading broke")
    assert KEY not in _kept(tmp_path) and not workdirs[0].exists()


def test_a_nonzero_exit_or_a_failed_result_fails_the_run(tmp_path: Path) -> None:
    command = AgentCommand(argv=lambda _config: [sys.executable, "-c", "raise SystemExit(3)"], timeout_s=30)
    with run_agent(command, mcp_config={}, base_env={}, secret=KEY, run_dir=tmp_path) as agent:
        assert (agent.status, agent.returncode) == ("failed: rc=3 subtype=None", 3)


def test_an_empty_secret_is_refused(tmp_path: Path) -> None:
    with (
        pytest.raises(ValueError, match="non-empty"),
        run_agent(_stub(), mcp_config={}, base_env={}, secret="", run_dir=tmp_path),
    ):
        pass
    with pytest.raises(ValueError, match="non-empty"):
        secret_scrub(tmp_path, "")


def test_scrub_removes_the_secret(tmp_path: Path) -> None:
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "log").write_text("key=sekrit-123 ok", encoding="utf-8")
    secret_scrub(tmp_path, "sekrit-123")
    assert (tmp_path / "a" / "log").read_text(encoding="utf-8") == "key=[REDACTED] ok"


# --- the outcome study and the bench through the shared runner ------------------------------------------


def _setup(tmp_path: Path, binary: list[str], *, timeout_s: float = 60, agent: str = "claude") -> ab_run.Setup:
    return ab_run.Setup(
        agent=agent,
        binary=binary,
        server_env={},
        base_env=arms.agent_env({"PATH": "/usr/bin:/bin", "HOME": str(tmp_path)}),
        secret=KEY,
        python3=sys.executable,
        timeout_s=timeout_s,
    )


TASK = tasks.load_task("j1-refund-window")


def _study_run(tmp_path: Path, binary: list[str], timeout_s: float = 60, arm: str = "A") -> dict[str, Any]:
    book = SpendLedger(ledger.POLICY, tmp_path / "ledger.json")
    return ab_run.run_one(TASK, arm, 1, _setup(tmp_path, binary, timeout_s=timeout_s), book, tmp_path / "out")


def test_a_study_run_goes_offline_with_a_stub_agent(tmp_path: Path) -> None:
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps({"calls": [], "answer": f"nothing changed {KEY}"}), encoding="utf-8")
    record = _study_run(tmp_path, [sys.executable, str(BENCH_AGENT), str(plan)])
    assert record["status"] == "ok" and not record["success"] and record["regressions"] == []
    assert record["measurement"] is None and record["reached_model"], "reached the model, then failed: measured"
    assert record["mcp_servers"] == {} and record["jev_calls"] == 0 and not record["jev_tool_called"]
    assert record["decision"] is None and record["decision_correct"] is False
    assert KEY not in _kept(tmp_path / "out")


def _sleeper(tmp_path: Path) -> list[str]:
    script = tmp_path / "sleeper.py"
    script.write_text("import time\ntime.sleep(30)\n", encoding="utf-8")
    return [sys.executable, str(script)]


def test_study_and_bench_time_out_the_same_way(tmp_path: Path) -> None:
    record = _study_run(tmp_path / "p", _sleeper(tmp_path), timeout_s=1)
    item = load_items(Path(__file__).resolve().parent / "data" / "bench-dryrun.jsonl")[0]
    setup = run.Setup(
        agent=lambda _item, _arm: _sleeper(tmp_path),
        server=[],
        server_env={},
        base_env={"PATH": "/usr/bin:/bin"},
        secret=KEY,
        timeout_s=1,
    )
    bench = run.run_one(item, "A", setup, SpendLedger(bench_ledger.POLICY, tmp_path / "ledger.json"), tmp_path / "b")
    assert record["status"] == bench["status"] == "failed: timeout after 1s"
    assert not bench["correct"] and not record["success"]
    assert record["measurement"] == "never reached the model", "a timeout with no model output is a harness fault"
    for timed_out in (record, bench):
        assert timed_out["agent_cost_reported"] is False and timed_out["cost_usd"] == ledger.RUN_BOUND_USD


@pytest.mark.parametrize("agent", ab_run.AGENTS)
def test_the_study_launches_its_agent_with_that_agents_cli_tail(tmp_path: Path, agent: str) -> None:
    setup = _setup(tmp_path, [sys.executable, "-c", STUB, "0"], agent=agent)
    book = SpendLedger(ledger.POLICIES[agent], tmp_path / "ledger.json")
    ab_run.run_one(TASK, "B", 1, setup, book, tmp_path / "out")
    lines = (tmp_path / "out" / "j1-refund-window.B.r1" / "stream.jsonl").read_text(encoding="utf-8").splitlines()
    events = [json.loads(line) for line in lines if line.startswith("{")]
    tail = json.loads(events[-1]["result"])["argv"]
    config = Path(tail[tail.index("--mcp-config") + 1])
    addendum = arms.addendum("B", agent, TASK.judgment.jev_tool)
    assert tail == ab_run.argv_tail(agent, ab_run.user_prompt(TASK), config, addendum)


def _reporting(servers: list[dict[str, str]]) -> list[str]:
    """A stand-in agent whose init event reports `servers`, then succeeds after one model call."""
    script = f"""
import json
print(json.dumps({{"type": "system", "subtype": "init", "model": "stub", "mcp_servers": {servers!r}}}))
print(json.dumps({{"type": "assistant", "message": {{"id": "m", "usage": {{"output_tokens": 3}}, "content": []}}}}))
print(json.dumps({{"type": "result", "subtype": "success", "is_error": False, "result": "done"}}))
"""
    return [sys.executable, "-c", script]


@pytest.mark.parametrize(
    ("servers", "expected"),
    [
        ([{"name": "jev", "status": "failed"}], "jev server failed"),
        ([{"name": "jev", "status": "connected"}], "Jev not used: no Jev call"),
        ([], "Jev not used: no Jev call"),  # Pi's stream names no servers: the proxy log decides
    ],
)
def test_a_with_jev_run_that_never_used_jev_is_not_measured(
    tmp_path: Path, servers: list[dict[str, str]], expected: str
) -> None:
    record = _study_run(tmp_path, _reporting(servers), arm="B")
    assert record["measurement"] == expected and record["status"] == "ok" and record["reached_model"]


@pytest.mark.parametrize(
    ("servers", "expected"),
    [
        ([{"name": "jev", "status": "failed"}], "failed: jev server failed"),
        ([{"name": "jev", "status": "connected"}], "ok"),
        ([], "ok"),  # Pi's stream names no servers: the check is skipped, the use gate still applies
    ],
)
def test_a_bench_forced_run_whose_server_is_not_connected_fails(
    tmp_path: Path, servers: list[dict[str, str]], expected: str
) -> None:
    item = load_items(Path(__file__).resolve().parent / "data" / "bench-dryrun.jsonl")[0]
    setup = run.Setup(
        agent=lambda _item, _arm: _reporting(servers),
        server=[],
        server_env={},
        base_env={"PATH": "/usr/bin:/bin"},
        secret=KEY,
        timeout_s=30,
    )
    record = run.run_one(item, "C", setup, SpendLedger(bench_ledger.POLICY, tmp_path / "ledger.json"), tmp_path / "b")
    assert record["status"] == expected and not record["correct"] and record["gate"] == "no Jev call"
    failed = run.failed_record(
        str(record["run_id"]),
        item,
        "C",
        AgentRunResult((), tmp_path, "", "", 0, 1.0, Trace(), "ok"),
        0.0,
        RuntimeError("span parse broke"),
    )
    assert list(record) == list(failed), "a failed bench row has the success row's keys"


@pytest.mark.parametrize("arm", arms.ARMS)
def test_a_study_run_whose_grading_raises_leaves_a_failed_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, arm: str
) -> None:
    def broken(*_args: object) -> None:
        raise RuntimeError(f"grading broke {KEY}")

    monkeypatch.setattr(ab_run, "grade", broken)
    with pytest.raises(RuntimeError, match="grading broke"):
        _study_run(tmp_path, _reporting([]), arm=arm)
    out = tmp_path / "out"
    assert KEY not in _kept(out)
    (failed,) = [json.loads(p.read_text(encoding="utf-8")) for p in out.glob("*/result.json")]
    assert failed["run_id"] == f"j1-refund-window.{arm}.r1" and not failed["success"]
    assert failed["status"].startswith("failed: post-processing raised RuntimeError: grading broke")
    assert failed["measurement"] == "harness error"
    assert failed["cost_usd"] == failed["agent_cost_usd"] == ledger.RUN_BOUND_USD
    assert failed["agent_cost_reported"] is False
    monkeypatch.undo()
    graded = _study_run(tmp_path / "graded", _reporting([]))
    assert list(failed) == list(graded), "the report reads the same keys, in the same order, from every record"
    assert "harness error" in report.render([failed], {})


def test_the_study_books_a_run_whose_grading_raises_and_never_relaunches_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(*_args: object) -> None:
        raise RuntimeError("grading broke")

    launches = tmp_path / "launches.log"
    launcher = tmp_path / "agent.sh"
    stub = tmp_path / "stub.py"
    stub.write_text("import json, sys\nsys.argv = [sys.argv[0], '0', *sys.argv[1:]]\n" + STUB, encoding="utf-8")
    launcher.write_text(f'#!/bin/sh\necho launch >> "{launches}"\nexec "{sys.executable}" "{stub}" "$@"\n', "utf-8")
    launcher.chmod(0o755)
    monkeypatch.setattr(ab_run, "grade", broken)
    setup = ab_run.Setup(
        agent="claude",
        binary=[str(launcher)],
        server_env={},
        base_env=arms.agent_env({"PATH": "/usr/bin:/bin", "HOME": str(tmp_path)}),
        secret=KEY,
        python3=sys.executable,
        task_list=[TASK],
    )
    out = tmp_path / "out"
    plan = ab_run.schedule([TASK], 1)
    first, second = (ab_run.run_id(TASK.id, arm, 1) for arm in plan[0][2])
    with pytest.raises(RuntimeError, match="grading broke"):
        ab_run.study(setup, out, repeats=1)
    kept = (out / first / "result.json").read_text(encoding="utf-8")
    with pytest.raises(RuntimeError, match="grading broke"):
        ab_run.study(setup, out, repeats=1)
    assert launches.read_text(encoding="utf-8").splitlines() == ["launch", "launch"], "one launch per run id"
    book = SpendLedger.load(out / "ledger.json", ledger.POLICY)
    assert book.runs == {first: ledger.RUN_BOUND_USD, second: ledger.RUN_BOUND_USD}
    assert (out / first / "result.json").read_text(encoding="utf-8") == kept
    assert ab_run.study(setup, out, repeats=1) == "all 1 pairs recorded", "resume runs nothing"
    assert KEY not in _kept(out)
