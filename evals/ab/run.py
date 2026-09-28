"""The agent outcome study: does Jev improve a coding agent while the agent uses the Jev MCP tools?

`JEV_AB_LIVE=1 make ab AGENT=claude|pi` runs it (paid); `python -m evals.ab.run --report-only`
re-renders `reports/agent-outcomes.md` offline from recorded runs.

One agent, both arms, the same tasks. Each task runs `REPEATS` times per arm, in A/B pairs whose
order is seeded and random; a pair starts only if the whole pair fits the agent's ledger, and a
recorded run, failed or not, is never retried, so an interrupted study resumes where it stopped. The
study refuses to resume when the Jev revision, the fixture, or the agent's pinned setup has changed
since its first run, so no pair spans two setups. Live runs execute inside the confinement boundary of
ADR-0074: a broker sidecar holding the TypeSafe key and the scoped model-provider key, a per-run
capability, and an agent container with no network and no credential inside (the macOS sandbox path
remains the offline dry-run shape). Before the first paid run, one confined preflight — a single
allowlisted provider request through the real broker — refuses the batch and books nothing when the
boundary is dead, so a broken broker or credential cannot spend the run cap. A study with nothing left
to launch skips it, and the preflight's cost is recorded in `preflight.json`, not the ledger. Every
kept artifact is scanned for the key value and scrubbed of it. `evals.study_runner` owns the run, the
result file, and the booking handler (ADR-0045).
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
import tempfile
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, cast

from evals.ab import arms, ledger, outcomes, report, stream, tasks, unsafe
from evals.ab.grade import grade, old_rule_success, run_correct
from evals.agent import (
    PREFLIGHT_TIMEOUT_S,
    AgentCommand,
    AgentPreflightError,
    AgentRunResult,
    AgentSetupError,
    real_agent_dir,
    run_agent,
    run_preflight,
    scoped_models,
    write_preflight,
)
from evals.bench import pi
from evals.confinement.launch import (
    CONTAINER_ADAPTER,
    ConfinementError,
    ConfinementSpec,
    Upstream,
    confined_reference,
    docker_available,
    exit_on_sigterm,
    image_present,
    probe_provider,
    reap,
    run_confined,
)
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
    """The Jev server's env in arm B (no key: live, the key reaches the server by sandbox keyfile)."""
    base_env: Mapping[str, str]
    """The agent's whole environment (`arms.agent_env` of the caller's)."""
    secret: str
    """Scrubbed from every kept artifact."""
    python3: str
    """The grader's interpreter."""
    auth_provider: str = ""
    """The one auth.json provider entry the agent's model needs; empty copies no auth."""
    server_python: str = ""
    """The study venv interpreter the sandbox launcher wraps; empty is the offline dry-run shape."""
    timeout_s: float = arms.RUN_TIMEOUT_S
    confinement: ConfinementSpec | None = None
    """The broker + container boundary live runs use (ADR-0074); None is the offline dry run."""
    model: str = ""
    """The study's pi model (`JEV_AB_MODEL`, default `pi.PI_MODEL`); empty means the default."""
    provider_secret: str = ""
    """The scoped provider key value, scanned and scrubbed alongside the TypeSafe key (F10)."""
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


def argv_tail(
    agent: str, prompt: str, config: Path, addendum: str, adapter: str | None = None, model: str = ""
) -> list[str]:
    """Everything after the agent binary. Arms differ only in `addendum` (and the config's contents).
    Confined runs pass the container-side adapter path; `model` overrides the pi default."""
    if agent == "pi":
        extra: dict[str, Any] = {}
        if adapter:
            extra["adapter"] = adapter
        if model:
            extra["model"] = model
        return pi.pi_command("pi", prompt, config, addendum, **extra)[1:]
    return arms.claude_command("claude", prompt, config, addendum)[1:]


def parser_for(agent: str) -> Callable[[Iterable[str]], stream.Trace]:
    return pi.parse if agent == "pi" else stream.parse


def run_id(task_id: str, arm: str, repeat: int) -> str:
    return f"{task_id}.{arm}.r{repeat}"


def preflight(setup: Setup, out: Path | None = None) -> AgentRunResult:
    """One prompt through `run_agent`'s own env, before the first paid run of a study.

    A preflight that never reaches the model refuses the batch and books nothing. When `out` is given,
    the cost is written to `preflight.json` either way, so a report can add it and a rerun of a
    finished study is not what records it. Only Claude gets the login-keychain link.
    """
    try:
        result = run_preflight(
            lambda config: [
                *setup.binary,
                *argv_tail(setup.agent, "Reply with the single word ok.", config, arms.SYSTEM_ADDENDUM),
            ],
            base_env=setup.base_env,
            secret=setup.secret,
            mcp_config={},
            login_keychain=setup.agent == "claude",
            parse=parser_for(setup.agent),
            timeout_s=PREFLIGHT_TIMEOUT_S,
            auth_provider=setup.auth_provider or None,
        )
    except AgentPreflightError as error:
        if out is not None:
            write_preflight(out, cost_usd=error.cost_usd, model=error.model)
        raise StudyRefusedError(
            f"{setup.agent} preflight could not reach its model ({error}); refusing the batch before any run is booked"
        ) from error
    if out is not None:
        write_preflight(out, cost_usd=_usd(result), model=result.trace.model)
    return result


def _usd(result: AgentRunResult) -> float | None:
    cost = result.trace.result_field("total_cost_usd")
    if isinstance(cost, bool) or not isinstance(cost, int | float):
        return None
    return float(cost)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


def run_one(task: tasks.Task, arm: str, repeat: int, setup: Setup, book: SpendLedger, out: Path) -> dict[str, Any]:
    rid = run_id(task.id, arm, repeat)
    run_dir = out / rid
    jev_log = run_dir / "jev-calls.jsonl"
    prompt = user_prompt(task)
    addendum = arms.addendum(arm, setup.agent, task.judgment.jev_tool)
    if setup.confinement is not None:
        spec = setup.confinement
        adapter = CONTAINER_ADAPTER if setup.agent == "pi" else None
        model_args = {"model": setup.model} if (setup.agent == "pi" and setup.model) else {}
        command = AgentCommand(
            argv=lambda config: [
                setup.agent,
                *argv_tail(setup.agent, prompt, config, addendum, adapter=adapter, **model_args),
            ],
            timeout_s=setup.timeout_s,
        )
        with run_confined(
            command,
            spec=spec,
            mcp_config=lambda scratch: arms.confined_mcp_config(
                arm,
                scratch=scratch,
                server_env=setup.server_env,
                typesafe_shim_url=spec.shim_url("typesafe"),
            ),
            scratch_files=_container_agent_files(spec, setup),
            secret=setup.secret,
            run_dir=run_dir,
            extra_secrets=(setup.provider_secret,) if setup.provider_secret else (),
            prepare=lambda workdir: tasks.materialize(task, workdir),
            parse=parser_for(setup.agent),
        ) as run:
            record = _record_inside(run_dir, run, rid, task, arm, repeat, setup, book, jev_log)
    else:
        command = AgentCommand(
            argv=lambda config: [*setup.binary, *argv_tail(setup.agent, prompt, config, addendum)],
            timeout_s=setup.timeout_s,
            login_keychain=setup.agent == "claude",
        )
        with run_agent(
            command,
            mcp_config=lambda sandbox: arms.mcp_config(
                arm,
                sandbox=sandbox,
                server_env=setup.server_env,
                api_key=setup.secret if arm == "B" else "",
                interpreter=setup.server_python or None,
            ),
            base_env=setup.base_env,
            secret=setup.secret,
            run_dir=run_dir,
            prepare=lambda workdir: tasks.materialize(task, workdir),
            parse=parser_for(setup.agent),
            auth_provider=setup.auth_provider or None,
        ) as run:
            record = _record_inside(run_dir, run, rid, task, arm, repeat, setup, book, jev_log)
    return record


def _record_inside(
    run_dir: Path,
    run: AgentRunResult,
    rid: str,
    task: tasks.Task,
    arm: str,
    repeat: int,
    setup: Setup,
    book: SpendLedger,
    jev_log: Path,
) -> dict[str, Any]:
    """Grade and write the run's record inside either runner's `with` block, while the workdir
    still exists. Shared by the macOS sandbox and the confined container path (ADR-0074)."""

    def failed(error: Exception) -> dict[str, Any]:
        return failed_record(rid, task, arm, repeat, setup.agent, run, book.policy.run_bound_usd, error)

    return write_record(
        run_dir / "result.json",
        lambda: _record(rid, task, arm, repeat, setup, run, book, jev_log, run_dir),
        failed,
    )


def _container_agent_files(spec: ConfinementSpec, setup: Setup) -> Callable[[Path], None]:
    """The scratch writer for a confined run: the container's placeholder auth.json/models.json,
    built from the operator's scoped entries with the provider base URL pointed at the shim."""

    def write(scratch: Path) -> None:
        if setup.agent != "pi" or not setup.auth_provider:
            return
        provider = next((u for u in spec.upstreams if u.name != "typesafe"), None)
        if provider is None:
            return
        real = real_agent_dir(setup.base_env)
        entry: dict[str, Any] = {}
        try:
            loaded: object = json.loads((real / "auth.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            loaded = {}
        if isinstance(loaded, dict):
            entries = cast(dict[str, Any], loaded)
            raw_entry: object = entries.get(setup.auth_provider)
            if isinstance(raw_entry, dict):
                entry = dict(cast(dict[str, Any], raw_entry))
        arms.container_agent_files(
            scratch / "agent",
            provider=setup.auth_provider,
            auth_entry=entry,
            models_bytes=scoped_models(real / "models.json", setup.auth_provider),
            provider_shim_url=spec.shim_url(provider.name),
        )

    return write


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
    "preexisting_altered",
    "added_tests",
    "old_rule_success",
    "failure_category",
    "unnecessary_jev_calls",
    "jev_answer",
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
    matches = outcomes.decision_correct(chosen, task.judgment)
    stream_path = run_dir / "stream.jsonl"
    stream_text = stream_path.read_text(encoding="utf-8") if stream_path.is_file() else ""
    answer = outcomes.jev_answer(stream_text, task.judgment.options, task.judgment.verdict_options)
    escaped = run.escape
    succeeded = run_correct(result, decision_matches_gold=matches) and not escaped
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
                arm,
                status=run.status,
                reached=reached,
                calls=calls,
                mcp_servers=trace.mcp_servers,
                control=task.judgment.control,
            ),
            "success": succeeded,
            "final_tests_passed": result.test_pass_rate == 1.0,
            "acceptance_passed": result.acceptance_passed,
            "acceptance_total": result.acceptance_total,
            "regressions": list(result.regressions),
            "protected_changed": list(result.protected_changed),
            "preexisting_altered": list(result.preexisting_altered),
            "added_tests": [
                {"file": item.file, "name": item.name, "outcome": item.outcome, "relevant": item.relevant}
                for item in result.added_tests
            ],
            "old_rule_success": old_rule_success(result),
            "failure_category": "escape"
            if escaped
            else outcomes.failure_category(
                status=run.status,
                success=succeeded,
                acceptance_passed=result.acceptance_passed,
                acceptance_total=result.acceptance_total,
                regressions=result.regressions,
                preexisting_altered=result.preexisting_altered,
                added_failing=bool(result.added_failing),
                added_irrelevant=bool(result.added_irrelevant),
                decision_matches_gold=matches,
                jev_calls=calls,
            ),
            "unnecessary_jev_calls": outcomes.unnecessary_jev_calls(
                control=task.judgment.control, uses=trace.tool_uses, calls=calls
            ),
            "jev_answer": answer,
            "jev_calls": len(outcomes.jev_rows(calls)),
            "jev_call_log": calls,
            "test_cycles": cycles,
            "retries": max(cycles - 1, 0),
            "wrong_branches": outcomes.wrong_branches(trace.tool_uses, task.judgment),
            "decision": chosen,
            "decision_correct": matches,
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
            "preexisting_altered": [],
            "added_tests": [],
            "old_rule_success": False,
            "failure_category": "escape" if getattr(run, "escape", None) else "harness error",
            "unnecessary_jev_calls": 0,
            "jev_answer": None,
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


def _load_book(
    setup: Setup, out: Path, *, repeats: int, seed: int
) -> tuple[SpendLedger, list[tuple[tasks.Task, int, tuple[str, ...]]]]:
    """The ledger after already-started runs are booked, and the plan those runs came from."""
    book = SpendLedger.load(out / "ledger.json", ledger.POLICIES[setup.agent])
    plan = schedule(setup.task_list, repeats, seed)
    for task, repeat, order in plan:
        for arm in order:
            rid = run_id(task.id, arm, repeat)
            if agent_started(out / rid):
                book_outcome(book, rid, out / rid / "result.json")
    return book, plan


def runs_remain(setup: Setup, out: Path, *, repeats: int = ledger.REPEATS, seed: int = SEED) -> bool:
    """True when a study call would launch at least one run. A finished study does not."""
    book, plan = _load_book(setup, out, repeats=repeats, seed=seed)
    return any(run_id(task.id, arm, repeat) not in book.runs for task, repeat, order in plan for arm in order)


def study(setup: Setup, out: Path, *, repeats: int = ledger.REPEATS, seed: int = SEED) -> str:
    """Run every pair not yet recorded, within the caps; returns why it stopped.

    A recorded escape stops the study: the agent left its boundary, so nothing recorded after it is
    comparable until the escape is understood. The guard also fires on resume, before any new run.
    """
    for path in sorted(out.glob("*/result.json")):
        record = json.loads(path.read_text(encoding="utf-8"))
        if record.get("failure_category") == "escape":
            return f"stopped: sandbox escape on {record.get('run_id')} (recorded earlier)"
    book, plan = _load_book(setup, out, repeats=repeats, seed=seed)
    for task, repeat, order in plan:
        pending = [arm for arm in order if run_id(task.id, arm, repeat) not in book.runs]
        if not pending:
            continue
        if not book.can_start(len(pending)):
            return f"stopped before {task.id} r{repeat}: {book.blocker(len(pending))}"
        for arm in pending:
            rid = run_id(task.id, arm, repeat)
            sys.stderr.write(f"run {setup.agent} {rid} (spent ${book.spent:.2f})\n")
            record = run_recorded(
                book,
                rid,
                out / rid / "result.json",
                lambda task=task, arm=arm, repeat=repeat: run_one(task, arm, repeat, setup, book, out),
            )
            if isinstance(record, Mapping) and record.get("failure_category") == "escape":
                return f"stopped: sandbox escape on {rid}"
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


def held_constant(agent: str, timeout_s: float, model: str = "") -> dict[str, str]:
    """What both arms share, per agent. The Jev integration (server + one addendum sentence) is the
    only difference; `tests/evals/test_ab_harness.py` checks the command lines and configs."""
    common = {
        "timeout": f"{timeout_s:.0f}s wall per run",
        "harness server (both arms)": "`request_human_review`",
        "system addendum (both arms)": arms.SYSTEM_ADDENDUM,
        "Jev (arm B only)": (
            f"`python -m jev_judge_mcp`, `JEV_PROVIDER=typesafe`, `JEV_MCP_MODEL={arms.JEV_MODEL}`; "
            "the server runs inside the confined agent container and reaches TypeSafe only through "
            "the broker sidecar over the shared Unix socket — no key, keyfile, or keychain exists "
            "inside the container, and the model credential crosses the same way (ADR-0074)"
        ),
        "grader": (
            "ADR-0073: hidden acceptance tests, no regressions, pre-existing test content unchanged, "
            "added tests must pass, decision matches gold (`evals/ab/grade.py`)"
        ),
    }
    if agent == "pi":
        return {
            **common,
            "model": f"`{model or pi.PI_MODEL}`, thinking `{pi.PI_THINKING}`",
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


def meta_for(agent: str, agent_version: str, timeout_s: float, model: str = "") -> dict[str, Any]:
    return {
        "jev_revision": _git_head(),
        "fixture_sha256": tasks.fixture_digest(),
        "agent": agent,
        "agent_version": agent_version,
        "held_constant": held_constant(agent, timeout_s, model),
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


EVAL_IMAGE = "jev-eval-agent:latest"
"""The confined agent runtime (docker/eval-agent.Dockerfile): python + the built wheel + node + the
agent CLIs. Build it with `make confinement-image`."""


def _confinement(
    environ: Mapping[str, str], agent: str, base_env: Mapping[str, str], auth_provider: str, out: Path
) -> tuple[ConfinementSpec, Path, str]:
    """The broker + container boundary live runs use (ADR-0074), plus the scoped provider key
    file the study must delete when it ends. Refuses (never bypasses) when a piece is missing."""
    unavailable = docker_available()
    if unavailable:
        raise StudyRefusedError(f"live runs are confined (ADR-0074) and need Docker: {unavailable}")
    image = environ.get("JEV_EVAL_IMAGE", EVAL_IMAGE)
    if not image_present(image):
        raise StudyRefusedError(f"the confined agent image {image} is not built; run `make confinement-image`")
    typesafe_key_file = environ.get("JEV_STUDY_KEY_FILE", "")
    if not typesafe_key_file or not Path(typesafe_key_file).is_file():
        raise StudyRefusedError("JEV_STUDY_KEY_FILE must name the study key file for the broker's read-only mount")
    upstreams: list[Upstream] = [Upstream.typesafe(typesafe_key_file)]
    scoped_dir = Path(tempfile.mkdtemp(prefix="jev-ab-secrets-"))
    scoped_dir.chmod(0o700)
    provider_key_file = scoped_dir / "provider.key"
    provider_secret = ""
    if agent == "pi":
        real = real_agent_dir(base_env)
        models: dict[str, Any] = {}
        try:
            loaded_models: object = json.loads((real / "models.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            loaded_models = {}
        if isinstance(loaded_models, dict):
            providers = cast(dict[str, Any], loaded_models).get("providers")
            if isinstance(providers, dict):
                models = cast(dict[str, Any], providers)
        entry: object = models.get(auth_provider)
        base_url = str(cast(dict[str, Any], entry).get("baseUrl") or "") if isinstance(entry, dict) else ""
        if not base_url.startswith(("http://", "https://")):
            raise StudyRefusedError(
                f"the confined pi arm needs a `{auth_provider}` provider entry with a baseUrl in the "
                f"operator's models.json ({real / 'models.json'}); define it there for the study's model"
            )
        key = arms.scoped_provider_key(real / "auth.json", auth_provider)
        if not key:
            raise StudyRefusedError(
                f"the confined pi arm needs an API-key auth.json entry for `{auth_provider}` "
                "(an OAuth login cannot cross the container boundary)"
            )
        provider_key_file.write_text(key + "\n", encoding="utf-8")
        provider_key_file.chmod(0o600)
        provider_secret = key
        upstreams.append(
            Upstream.provider(
                auth_provider, base_url, str(provider_key_file), api=str(cast(dict[str, Any], entry).get("api", ""))
            )
        )
    else:
        claude_key = environ.get("JEV_CLAUDE_KEY_FILE", "")
        if not claude_key or not Path(claude_key).is_file():
            raise StudyRefusedError(
                "the Claude arm's macOS keychain login cannot cross the container boundary (ADR-0074); "
                "set JEV_CLAUDE_KEY_FILE to a file holding the Anthropic API key, or run the pi arm"
            )
        # The operator names the Anthropic key file itself, so it mounts directly (F10) — the
        # only written scoped file is the pi arm's, whose source is the whole-operator auth.json.
        claude_secret = Path(claude_key).read_text(encoding="utf-8-sig").strip()
        provider_secret = claude_secret
        upstreams.append(
            Upstream.provider(
                "anthropic",
                environ.get("JEV_ANTHROPIC_BASE_URL", "https://api.anthropic.com"),
                claude_key,
                api="anthropic",
            )
        )
    env: dict[str, str] = {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "HOME": "/scratch/home",
        "TMPDIR": "/scratch/tmp",
        "TERM": "dumb",
    }
    if agent == "pi":
        env["PI_CODING_AGENT_DIR"] = "/scratch/agent"
    else:
        env.update(
            {
                "ANTHROPIC_BASE_URL": "http://127.0.0.1:8080",
                "ANTHROPIC_API_KEY": arms.PLACEHOLDER_KEY,
                "CLAUDE_CONFIG_DIR": "/scratch/claude",
            }
        )
    spec = ConfinementSpec(
        agent_image=image,
        upstreams=tuple(upstreams),
        timeout_s=arms.RUN_TIMEOUT_S,
        env=env,
        uid=os.getuid(),
        gid=os.getgid(),
    )
    return spec, scoped_dir, provider_secret


def live(environ: Mapping[str, str], out: Path, *, agent: str, repeats: int = ledger.REPEATS) -> str:
    if environ.get(LIVE_FLAG) != "1":
        raise StudyRefusedError(f"the study calls paid providers; set {LIVE_FLAG}=1 to run it")
    try:
        key = arms.study_key(environ)
    except ValueError as error:
        raise StudyRefusedError(str(error)) from error
    if agent not in AGENTS:
        raise StudyRefusedError(f"unknown agent {agent!r}; one of {', '.join(AGENTS)}")
    base_env = arms.agent_env(environ)
    agent_path = base_env["PATH"]
    binary = _which(agent, agent_path)
    python3 = _which("python3", agent_path)
    if agent == "pi":
        try:
            pi.adapter_path()
        except pi.AdapterMissing as error:
            raise StudyRefusedError(str(error)) from error
    version = subprocess.run([binary, "--version"], capture_output=True, text=True, check=False).stdout.strip()
    agent_out = out / agent
    interpreter = arms.study_venv(Path(tempfile.gettempdir()) / f"jev-study-{_git_head()[:12]}", arms.ensure_wheel())
    # JEV_AB_MODEL selects the study's pi model (the D3 re-run pins opencode-go/deepseek-v4.1-flash
    # instead of the host-loopback ds4 default); the provider entry is derived from it.
    model = environ.get("JEV_AB_MODEL", pi.PI_MODEL)
    auth_provider = model.split("/")[0] if agent == "pi" else ""
    setup = Setup(
        agent=agent,
        binary=[binary],
        server_env={"JEV_PROVIDER": "typesafe", "JEV_MCP_MODEL": arms.JEV_MODEL},
        base_env=base_env,
        secret=key,
        python3=python3,
        auth_provider=auth_provider,
        server_python=str(interpreter),
        model=model,
    )
    # A finished study has nothing left to launch: it pins and re-renders with no boundary built,
    # so a resume or a report pass never needs Docker.
    if not runs_remain(setup, agent_out, repeats=repeats):
        pin_meta(agent_out, meta_for(agent, version, arms.RUN_TIMEOUT_S, model))
        return study(setup, agent_out, repeats=repeats)
    # The confinement boundary is how live runs execute (ADR-0074); the macOS sandbox path stays
    # the offline dry-run shape only. A killed earlier run's orphans go first (F5).
    reap()
    spec, scoped_dir, provider_secret = _confinement(environ, agent, base_env, auth_provider, out)
    setup = replace(setup, confinement=spec, provider_secret=provider_secret)
    try:
        # Reference grading on the agent image's interpreter (F1): every task's reference solution
        # must pass there before any run is booked, exactly as the host-side check did.
        for task in tasks.load_tasks():
            confined_reference(spec.agent_image, task.id)
        # Preflight before pin_meta: a dead path must not pin a setup that has no runs, or the next
        # resume is refused for an agent_version that never recorded anything. Confined, the
        # preflight is one allowlisted provider request through the real boundary — no spend, no
        # agent run — so a dead broker, image, or credential refuses before anything is booked.
        detail = probe_provider(spec)
        if detail is not None:
            write_preflight(agent_out, cost_usd=None, model=None)
            raise StudyRefusedError(
                f"confined preflight could not reach the model provider through the broker ({detail}); "
                "refusing the batch before any run is booked"
            )
        pin_meta(agent_out, meta_for(agent, version, arms.RUN_TIMEOUT_S, model))
        return study(setup, agent_out, repeats=repeats)
    finally:
        shutil.rmtree(scoped_dir, ignore_errors=True)


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
    exit_on_sigterm()
    try:
        if not args.report_only:
            stop = live(environ, OUT, agent=args.agent, repeats=args.repeats)
            sys.stderr.write(f"{stop}\n")
        write_report(OUT)
    except (StudyRefusedError, AgentSetupError, ConfinementError) as refusal:
        sys.stderr.write(f"refused: {refusal}\n")
        return 2
    sys.stderr.write(f"report: {REPORT}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
