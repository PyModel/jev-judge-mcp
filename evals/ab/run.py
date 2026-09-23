"""The agent outcome study: does Jev improve a coding agent while the agent uses the Jev MCP tools?

`JEV_AB_LIVE=1 make ab AGENT=claude|pi` runs it (paid); `python -m evals.ab.run --report-only`
re-renders `reports/agent-outcomes.md` offline from recorded runs.

One agent, both arms, the same tasks. Each task runs `REPEATS` times per arm, in A/B pairs whose
order is seeded and random; a pair starts only if the whole pair fits the agent's ledger, and a
recorded run, failed or not, is never retried, so an interrupted study resumes where it stopped. The
study refuses to resume when the Jev revision, the fixture, or the agent's pinned setup has changed
since its first run, so no pair spans two setups. The TypeSafe key reaches only the Jev server's env,
through the 0600 config file `evals.agent.run_agent` writes and deletes; every kept artifact is
scrubbed of it. `evals.study_runner` owns the run, the result file, and the booking handler (ADR-0045).
"""

import argparse
import datetime
import json
import os
import platform
import random
import shutil
import subprocess
import sys
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from evals.ab import arms, ledger, outcomes, report, stream, tasks, unsafe
from evals.ab.grade import expected_ids, grade
from evals.agent import AgentCommand, AgentRunResult, run_agent
from evals.bench import pi
from evals.spend import SpendLedger, agent_started, book_outcome
from evals.study_runner import record_row, run_recorded, write_record

LIVE_FLAG = "JEV_AB_LIVE"
AGENTS = ("claude", "pi")
OUT = arms.REPO_ROOT / "evals" / "reports" / "agent-outcomes"
REPORT = arms.REPO_ROOT / "evals" / "reports" / "agent-outcomes.md"
SEED = 8
REVIEW_TOOL = "mcp__harness__request_human_review"
PINNED = ("jev_revision", "fixture_sha256", "agent", "agent_version", "held_constant", "hardware")
"""Meta fields a resumed study must match exactly."""


class StudyRefusedError(RuntimeError):
    pass


@dataclass(frozen=True)
class Setup:
    """What differs between the live study and the offline dry run."""

    agent: str
    """`claude` or `pi`: which command tail and stream parser the runs use."""
    binary: Sequence[str]
    """The command that stands in for the agent binary: the resolved path live, a stub offline."""
    server_env: Mapping[str, str]
    """The Jev server's env in arm B (`arms.jev_env` live)."""
    base_env: Mapping[str, str]
    """The agent's whole environment (`arms.agent_env` of the caller's)."""
    secret: str
    """Scrubbed from every kept artifact."""
    python3: str
    """The grader's interpreter."""
    timeout_s: float = arms.RUN_TIMEOUT_S
    task_list: Sequence[tasks.Task] = field(default_factory=tasks.load_tasks)

    def __post_init__(self) -> None:
        if self.agent not in AGENTS:
            raise ValueError(f"unknown agent {self.agent!r}")
        if not self.secret:
            raise ValueError("Setup.secret must be the non-empty key to scrub")


def user_prompt(task: tasks.Task) -> str:
    """The same user message in both arms: the task, how to test, and how to state the decision."""
    judgment = task.judgment
    options = "\n".join(f"- `{option}`: {text}" for option, text in judgment.options.items())
    return (
        f"{task.prompt}\n\nRun the tests with `{tasks.TEST_COMMAND}`.\n\n"
        f"The judgment this task hinges on: {judgment.question} Options:\n{options}\n\n"
        'When you are done, end your reply with one line of JSON and nothing after it: {"decision": "<option>"}.'
    )


def argv_tail(agent: str, prompt: str, config: Path, addendum: str) -> list[str]:
    """Everything after the agent binary. Arms differ only in `addendum` (and the config's contents)."""
    if agent == "pi":
        return pi.pi_command("pi", prompt, config, addendum)[1:]
    return arms.claude_command("claude", prompt, config, addendum)[1:]


def parser_for(agent: str) -> Callable[[Iterable[str]], stream.Trace]:
    return pi.parse if agent == "pi" else stream.parse


def run_id(task_id: str, arm: str, repeat: int) -> str:
    return f"{task_id}.{arm}.r{repeat}"


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


def run_one(task: tasks.Task, arm: str, repeat: int, setup: Setup, book: SpendLedger, out: Path) -> dict[str, Any]:
    rid = run_id(task.id, arm, repeat)
    run_dir = out / rid
    jev_log = run_dir / "jev-calls.jsonl"
    prompt = user_prompt(task)
    addendum = arms.addendum(arm, setup.agent, task.judgment.jev_tool)
    command = AgentCommand(
        argv=lambda config: [*setup.binary, *argv_tail(setup.agent, prompt, config, addendum)],
        timeout_s=setup.timeout_s,
    )
    with run_agent(
        command,
        mcp_config=arms.mcp_config(arm, jev_log=jev_log, server_env=setup.server_env),
        base_env=setup.base_env,
        secret=setup.secret,
        run_dir=run_dir,
        prepare=lambda workdir: tasks.materialize(task, workdir),
        parse=parser_for(setup.agent),
    ) as run:

        def failed(error: Exception) -> dict[str, Any]:
            return failed_record(rid, task, arm, repeat, setup.agent, run, book.policy.run_bound_usd, error)

        record = write_record(
            run_dir / "result.json",
            lambda: _record(rid, task, arm, repeat, setup, run, book, jev_log, run_dir),
            failed,
        )
    return record


_RECORD_KEYS = (
    "run_id",
    "task",
    "agent",
    "arm",
    "repeat",
    "status",
    "reached_model",
    "jev_tool_called",
    "jev_gate",
    "measurement",
    "success",
    "final_tests_passed",
    "acceptance_passed",
    "acceptance_total",
    "regressions",
    "protected_changed",
    "wall_s",
    "tokens",
    "tool_calls",
    "jev_calls",
    "jev_call_log",
    "test_cycles",
    "retries",
    "wrong_branches",
    "decision",
    "decision_correct",
    "agent_model",
    "agent_cost_usd",
    "jev_cost_usd",
    "cost_usd",
    "agent_cost_reported",
    "num_turns",
    "tool_counts",
    "mcp_servers",
    "unsafe",
    "human_review_requests",
)


def _outcome_record(
    rid: str,
    task_id: str,
    agent: str,
    arm: str,
    repeat: int,
    run: AgentRunResult,
    measured: Mapping[str, Any],
) -> dict[str, Any]:
    """The success record's keys. Failure fills `measured` and leaves the trace fields here."""
    trace = run.trace
    values: dict[str, Any] = {
        "run_id": rid,
        "task": task_id,
        "agent": agent,
        "arm": arm,
        "repeat": repeat,
        "reached_model": outcomes.reached_model(trace),
        "wall_s": run.wall_s,
        "tokens": outcomes.tokens(trace),
        "tool_calls": len(trace.tool_uses),
        "agent_model": trace.model,
        "num_turns": trace.result_field("num_turns"),
        "tool_counts": dict(trace.tool_counts),
        "mcp_servers": trace.mcp_servers,
        "human_review_requests": trace.tool_counts.get(REVIEW_TOOL, 0),
    }
    values.update(measured)
    return record_row(_RECORD_KEYS, values)


def _record(
    rid: str,
    task: tasks.Task,
    arm: str,
    repeat: int,
    setup: Setup,
    run: AgentRunResult,
    book: SpendLedger,
    jev_log: Path,
    run_dir: Path,
) -> dict[str, Any]:
    workdir, trace = run.workdir, run.trace
    subprocess.run(["git", "add", "-A"], cwd=workdir, check=False, capture_output=True)
    patch = subprocess.run(["git", "diff", "--cached"], cwd=workdir, capture_output=True, text=True, check=False).stdout
    (run_dir / "diff.patch").write_text(patch, encoding="utf-8")
    result = grade(workdir, task, setup.python3)
    protected = frozenset(tasks.protected_files())
    calls = _read_jsonl(jev_log)
    reached = outcomes.reached_model(trace)
    chosen = outcomes.decision(trace.result_field("result"), task.judgment)
    cycles = outcomes.test_cycles(trace.tool_uses)
    cost = book.cost_of(trace.result_field("total_cost_usd"), calls)
    return _outcome_record(
        rid,
        task.id,
        setup.agent,
        arm,
        repeat,
        run,
        {
            "status": run.status,
            "jev_tool_called": bool(outcomes.jev_rows(calls)),
            "jev_gate": outcomes.jev_gate(calls) if arm == "B" else None,
            "measurement": outcomes.measurement(
                arm, status=run.status, reached=reached, calls=calls, mcp_servers=trace.mcp_servers
            ),
            "success": result.correct,
            "final_tests_passed": result.test_pass_rate == 1.0,
            "acceptance_passed": result.acceptance_passed,
            "acceptance_total": result.acceptance_total,
            "regressions": list(result.regressions),
            "protected_changed": list(result.protected_changed),
            "jev_calls": len(outcomes.jev_rows(calls)),
            "jev_call_log": calls,
            "test_cycles": cycles,
            "retries": max(cycles - 1, 0),
            "wrong_branches": outcomes.wrong_branches(trace.tool_uses, task.judgment),
            "decision": chosen,
            "decision_correct": outcomes.decision_correct(chosen, task.judgment),
            "agent_cost_usd": cost.agent_usd,
            "jev_cost_usd": cost.jev_usd,
            "cost_usd": cost.total_usd,
            "agent_cost_reported": cost.agent_reported,
            "unsafe": [
                hit for use in trace.tool_uses for hit in unsafe.classify(use.name, use.input, str(workdir), protected)
            ],
        },
    )


def failed_record(
    rid: str,
    task: tasks.Task,
    arm: str,
    repeat: int,
    agent: str,
    run: AgentRunResult,
    run_bound: float,
    error: Exception,
) -> dict[str, Any]:
    """A run whose grading or accounting raised: a harness error, unmeasured, charged the run bound.

    Keys are the success record's (`_outcome_record`). The study runner books the bound.
    """
    return _outcome_record(
        rid,
        task.id,
        agent,
        arm,
        repeat,
        run,
        {
            "status": f"failed: post-processing raised {type(error).__name__}: {error}",
            "jev_tool_called": False,
            "jev_gate": None,
            "measurement": "harness error",
            "success": False,
            "final_tests_passed": False,
            "acceptance_passed": 0,
            "acceptance_total": 0,
            "regressions": [],
            "protected_changed": [],
            "jev_calls": 0,
            "jev_call_log": [],
            "test_cycles": 0,
            "retries": 0,
            "wrong_branches": None,
            "decision": None,
            "decision_correct": None,
            "agent_cost_usd": run_bound,
            "jev_cost_usd": 0.0,
            "cost_usd": run_bound,
            "agent_cost_reported": False,
            "unsafe": [],
        },
    )


def schedule(
    task_list: Sequence[tasks.Task], repeats: int, seed: int = SEED
) -> list[tuple[tasks.Task, int, tuple[str, ...]]]:
    """Repeat-major pairs, tasks shuffled within each repeat, arm order random within each pair."""
    rng = random.Random(seed)
    plan: list[tuple[tasks.Task, int, tuple[str, ...]]] = []
    for repeat in range(1, repeats + 1):
        order = list(task_list)
        rng.shuffle(order)
        plan += [(task, repeat, arms.ARMS if rng.random() < 0.5 else arms.ARMS[::-1]) for task in order]
    return plan


def study(setup: Setup, out: Path, *, repeats: int = ledger.REPEATS, seed: int = SEED) -> str:
    """Run every pair not yet recorded, within the caps; returns why it stopped."""
    book = SpendLedger.load(out / "ledger.json", ledger.POLICIES[setup.agent])
    plan = schedule(setup.task_list, repeats, seed)
    for task, repeat, order in plan:
        for arm in order:
            rid = run_id(task.id, arm, repeat)
            run_dir = out / rid
            if agent_started(run_dir):
                book_outcome(book, rid, run_dir / "result.json")
    for task, repeat, order in plan:
        pending = [arm for arm in order if run_id(task.id, arm, repeat) not in book.runs]
        if not pending:
            continue
        if not book.can_start(len(pending)):
            return f"stopped before {task.id} r{repeat}: {book.blocker(len(pending))}"
        for arm in pending:
            rid = run_id(task.id, arm, repeat)
            sys.stderr.write(f"run {setup.agent} {rid} (spent ${book.spent:.2f})\n")
            run_recorded(
                book,
                rid,
                out / rid / "result.json",
                lambda task=task, arm=arm, repeat=repeat: run_one(task, arm, repeat, setup, book, out),
            )
    return f"all {len(plan)} pairs recorded"


def load_records(out: Path) -> list[dict[str, Any]]:
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(out.glob("*/*/result.json"))]


def load_meta(out: Path) -> dict[str, dict[str, Any]]:
    return {p.parent.name: json.loads(p.read_text(encoding="utf-8")) for p in sorted(out.glob("*/meta.json"))}


def _git_head() -> str:
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=arms.REPO_ROOT, capture_output=True, text=True, check=False
    ).stdout.strip()
    dirty = subprocess.run(
        ["git", "status", "--porcelain", "--", "src", "evals"],
        cwd=arms.REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    ).stdout.strip()
    return f"{head}{'+dirty' if dirty else ''}"


def held_constant(agent: str, timeout_s: float) -> dict[str, str]:
    """What both arms share, per agent. The Jev integration (server + one addendum sentence) is the
    only difference; `tests/evals/test_ab_harness.py` checks the command lines and configs."""
    common = {
        "timeout": f"{timeout_s:.0f}s wall per run",
        "harness server (both arms)": "`request_human_review`",
        "system addendum (both arms)": arms.SYSTEM_ADDENDUM,
        "Jev (arm B only)": f"`python -m jev_judge_mcp`, `JEV_PROVIDER=typesafe`, `JEV_MCP_MODEL={arms.JEV_MODEL}`",
        "grader": "pristine pre-existing tests + hidden acceptance tests (`evals/ab/grade.py`)",
    }
    if agent == "pi":
        return {
            **common,
            "model": f"`{pi.PI_MODEL}`, thinking `{pi.PI_THINKING}`",
            "temperature": "Pi's default for the model, identical in both arms",
            "tools": "Pi's built-in tools + `pi-mcp-adapter` (loaded in both arms)",
            "limits": "no turn or token cap exposed by `pi --print`; the timeout bounds the run",
        }
    return {
        **common,
        "model": f"`{arms.AGENT_MODEL}`, effort `{arms.AGENT_EFFORT}`",
        "temperature": arms.AGENT_TEMPERATURE,
        "tools": ", ".join(arms.BUILTIN_TOOLS),
        "limits": f"{arms.MAX_TURNS} turns, ${arms.RUN_BUDGET_USD:.2f} (`--max-budget-usd`)",
    }


def meta_for(agent: str, agent_version: str, timeout_s: float) -> dict[str, Any]:
    return {
        "jev_revision": _git_head(),
        "fixture_sha256": tasks.fixture_digest(),
        "agent": agent,
        "agent_version": agent_version,
        "held_constant": held_constant(agent, timeout_s),
        "hardware": f"{platform.platform()}, {platform.machine()}, {os.cpu_count()} CPUs",
    }


def pin_meta(out: Path, current: Mapping[str, Any]) -> None:
    """Write the study's meta on its first run; refuse a resume whose pinned fields differ."""
    path = out / "meta.json"
    if path.exists():
        stored = json.loads(path.read_text(encoding="utf-8"))
        changed = [key for key in PINNED if stored.get(key) != current.get(key)]
        if changed:
            raise StudyRefusedError(
                f"{out} was recorded under a different {', '.join(changed)}; pairs must not span two setups. "
                "Move the directory aside to start a new study"
            )
        return
    out.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({**current, "started": datetime.date.today().isoformat()}, indent=2) + "\n", "utf-8")


def _which(name: str, path: str) -> str:
    found = shutil.which(name, path=path)
    if found is None:
        raise StudyRefusedError(f"{name} not found on PATH")
    return found


def live(environ: Mapping[str, str], out: Path, *, agent: str, repeats: int = ledger.REPEATS) -> str:
    if environ.get(LIVE_FLAG) != "1":
        raise StudyRefusedError(f"the study calls paid providers; set {LIVE_FLAG}=1 to run it")
    key = environ.get("TYPESAFE_API_KEY")
    if not key:
        raise StudyRefusedError("TYPESAFE_API_KEY is not set")
    if agent not in AGENTS:
        raise StudyRefusedError(f"unknown agent {agent!r}; one of {', '.join(AGENTS)}")
    base_env = arms.agent_env(environ)
    agent_path = base_env["PATH"]
    binary = _which(agent, agent_path)
    python3 = _which("python3", agent_path)
    if agent == "pi" and not pi.ADAPTER.is_file():
        raise StudyRefusedError(f"pi MCP adapter not found at {pi.ADAPTER}")
    for task in tasks.load_tasks():
        expected_ids(task, python3)
    version = subprocess.run([binary, "--version"], capture_output=True, text=True, check=False).stdout.strip()
    agent_out = out / agent
    pin_meta(agent_out, meta_for(agent, version, arms.RUN_TIMEOUT_S))
    setup = Setup(
        agent=agent,
        binary=[binary],
        server_env=arms.jev_env(api_key=key, path=agent_path),
        base_env=base_env,
        secret=key,
        python3=python3,
    )
    return study(setup, agent_out, repeats=repeats)


def write_report(out: Path, path: Path = REPORT) -> str:
    text = report.render(load_records(out), load_meta(out))
    path.write_text(text, encoding="utf-8")
    return text


def main(argv: Sequence[str] | None = None, environ: Mapping[str, str] = os.environ) -> int:
    parser = argparse.ArgumentParser(prog="python -m evals.ab.run", description=__doc__)
    parser.add_argument("--agent", choices=AGENTS, default="claude")
    parser.add_argument("--repeats", type=int, default=ledger.REPEATS)
    parser.add_argument("--report-only", action="store_true", help="re-render the report from recorded runs")
    args = parser.parse_args(argv)
    try:
        if not args.report_only:
            stop = live(environ, OUT, agent=args.agent, repeats=args.repeats)
            sys.stderr.write(f"{stop}\n")
        write_report(OUT)
    except StudyRefusedError as refusal:
        sys.stderr.write(f"refused: {refusal}\n")
        return 2
    sys.stderr.write(f"report: {REPORT}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
