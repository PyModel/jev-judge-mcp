"""The outcome study's offline dry run: `evals.ab.run.study` end to end with a stub agent and a loopback provider.

Each run spawns the stub (`tests/support/bench_agent.py`) in place of `claude`. In arm B the stub goes
through the recording proxy to the real `python -m jev_judge_mcp`, pointed at `tests/support/bench_provider.py`
on 127.0.0.1, so the proxy log, not the stub's text, decides whether Jev was used. The stub writes the
task's reference solution (or a wrong one) and runs the tests, so the real grader scores it. No model,
no provider, no paid call. The records are stub data: nothing here is a study result.
"""

import json
import os
import sys
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from evals.ab import arms, report, tasks
from evals.ab import run as ab_run
from tests.support.bench_provider import API_KEY, Loopback

REPO = Path(__file__).resolve().parents[2]
AGENT = REPO / "tests" / "support" / "bench_agent.py"
TASK = tasks.load_task("j1-refund-window")
GOLD = str(TASK.judgment.gold)

Plan = dict[str, Any]
Planner = Callable[[str, int], Plan]


def _solution(option: str) -> dict[str, str]:
    root = TASK.reference if option == GOLD else TASK.distractors[option]
    return {path.name: path.read_text(encoding="utf-8") for path in root.iterdir()}


def solve(option: str = GOLD, *, call_jev: bool = True) -> Planner:
    def plan(arm: str, _repeat: int) -> Plan:
        files = _solution(option)
        calls = (
            [{"tool": "jev_verify", "arguments": {"claims": ["Day 30 after delivery is on time."], "evidence": "x"}}]
            if arm == "B" and call_jev
            else []
        )
        uses = [{"name": "Write", "input": {"file_path": "refunds.py", "content": text}} for text in files.values()] + [
            {"name": "Bash", "input": {"command": tasks.TEST_COMMAND}}
        ]
        answer = f"Changed refunds.py.\n{json.dumps({'decision': option})}"
        return {"calls": calls, "files": files, "uses": uses, "answer": answer}

    return plan


def setup(tmp: Path, loopback: Loopback, planner: Planner) -> ab_run.Setup:
    # The runner fixes the binary per study, so a dispatcher picks the plan from the arm it can see in
    # the argv: arm B's addendum names the Jev tool.
    plans = {arm: planner(arm, 1) for arm in arms.ARMS}
    for arm, plan in plans.items():
        (tmp / f"plan-{arm}.json").write_text(json.dumps(plan), encoding="utf-8")
    plan_path = f"os.path.join({str(tmp)!r}, f'plan-{{arm}}.json')"
    (tmp / "dispatch.py").write_text(
        "import os, sys\n"
        "arm = 'B' if any('mcp__jev__' in a for a in sys.argv) else 'A'\n"
        f"os.execv(sys.executable, [sys.executable, {str(AGENT)!r}, {plan_path}, *sys.argv[1:]])\n",
        encoding="utf-8",
    )
    env = {
        **loopback.env(),
        "JEV_MCP_MODEL": arms.JEV_MODEL,
        "PYTHONPATH": str(REPO),
        "PATH": os.environ.get("PATH", ""),
    }
    return ab_run.Setup(
        agent="claude",
        binary=[sys.executable, str(tmp / "dispatch.py")],
        server_env=env,
        base_env=arms.agent_env(os.environ),
        secret=API_KEY,
        python3=sys.executable,
        timeout_s=120,
        task_list=[TASK],
    )


@pytest.fixture
def loopback() -> Iterator[Loopback]:
    with Loopback() as endpoint:
        yield endpoint


def _records(out: Path) -> dict[str, dict[str, Any]]:
    return {r["run_id"]: r for r in (json.loads(p.read_text(encoding="utf-8")) for p in out.glob("*/result.json"))}


def test_dry_run_measures_a_pair_where_the_agent_used_jev(tmp_path: Path, loopback: Loopback) -> None:
    out = tmp_path / "out" / "claude"
    assert ab_run.study(setup(tmp_path, loopback, solve()), out, repeats=1) == "all 1 pairs recorded"
    got = _records(out)
    a, b = got["j1-refund-window.A.r1"], got["j1-refund-window.B.r1"]
    for record in (a, b):
        assert record["status"] == "ok" and record["measurement"] is None
        assert record["success"] and record["final_tests_passed"] and record["decision_correct"]
        assert (record["test_cycles"], record["retries"], record["wrong_branches"]) == (1, 0, [])
    assert (a["jev_calls"], a["jev_tool_called"], a["jev_call_log"]) == (0, False, [])
    assert (b["jev_calls"], b["jev_tool_called"], b["jev_gate"]) == (1, True, None)
    assert b["jev_call_log"][0]["model"] == arms.JEV_MODEL and loopback.requests >= 1
    assert b["tool_calls"] == a["tool_calls"] + 1
    assert API_KEY not in "".join(p.read_text(encoding="utf-8") for p in out.rglob("*") if p.is_file())
    text = report.render(ab_run.load_records(tmp_path / "out"), {})
    assert "Measured pairs: 1." in text and "| tasks solved | 1/1 | 1/1 |" in text
    assert "| judge accuracy (decision = gold) | 1/1 | 1/1 |" in text


def test_dry_run_where_the_agent_skips_jev_is_not_measured(tmp_path: Path, loopback: Loopback) -> None:
    out = tmp_path / "out" / "claude"
    ab_run.study(setup(tmp_path, loopback, solve(call_jev=False)), out, repeats=1)
    b = _records(out)["j1-refund-window.B.r1"]
    assert b["success"] and b["measurement"] == "Jev not used: no Jev call" and loopback.requests == 0
    text = report.render(ab_run.load_records(tmp_path / "out"), {})
    assert "**Not measured.**" in text and "### Outcomes" not in text


def test_dry_run_scores_a_wrong_judgment_as_a_measured_failure(tmp_path: Path, loopback: Loopback) -> None:
    out = tmp_path / "out" / "claude"
    ab_run.study(setup(tmp_path, loopback, solve("delivery-date-exclusive")), out, repeats=1)
    for record in _records(out).values():
        assert record["measurement"] is None and not record["success"]
        assert record["regressions"] == [] and record["acceptance_passed"] < record["acceptance_total"]
        assert record["wrong_branches"] == ["delivery-date-exclusive"] and record["decision_correct"] is False
