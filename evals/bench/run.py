"""The bench runner: `JEV_BENCH_LIVE=1 python -m evals.bench.run` (paid; not wired to any make target).

Items run in a seeded random order. Each item's A, B, and C runs go back to back in a random order
within the triplet, and a triplet starts only if the whole triplet fits the ledger, so a stop leaves
complete triplets. A is direct (no Jev server). B is automatic: the server is available, and a run
that never calls Jev is recorded as "did not call Jev", not a failure (ADR-0036). C is forced: the
server plus the one-sentence instruction, and a run with no Jev answer fails the use gate.

The bench model is Pi `opencode-go/deepseek-v4.1-flash` at thinking high. One Pi prompt preflights
that model. The first provider connection, auth, or rate-limit error stops the run. That attempt is
booked so it is never retried, and it is not an arm result. Only complete triplets are analyzed.
After the first 10 triplets the forced-arm compliance stop and the Jev-spend projection apply. The
25 USD ceiling applies to Jev spend. Agent model spend is recorded separately from Pi's usage cost
fields, and marked not measured when those fields are absent.

On 2026-09-22 a two-arm Pi run reached its model for 43 with-Jev runs, then the provider stopped
accepting connections. Pi reported "Connection error." with zero tokens on both arms, and the runner
kept the remaining 107 as if they were results. This runner does not do that again.

The paid path refuses without its flag, without `TYPESAFE_API_KEY`, and while any item's label is
not `frozen`. The offline dry run (`tests/evals/test_bench_dryrun.py`) drives this same code with a
stub agent and a loopback provider. `evals.study_runner` owns the run, the result file, and the
booking handler (ADR-0045).
"""

import argparse
import json
import os
import random
import re
import shutil
import sys
import tempfile
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from evals.ab import arms, stream
from evals.agent import AgentCommand, AgentRunResult, run_agent
from evals.bench import analysis, answer, chart, gate, prompt, spans
from evals.bench.items import Item, load_items
from evals.bench.ledger import ARMS, POLICY, TRIPLET
from evals.spend import SpendLedger, agent_started, book_outcome
from evals.study_runner import record_row, run_recorded, write_record

LIVE_FLAG = "JEV_BENCH_LIVE"
OUT = arms.REPO_ROOT / "evals" / "reports" / "bench150"
SEED = 150
EARLY_PAIRS = 10
COMPLIANCE_STOP = 3
COST_STOP_USD = POLICY.max_usd - POLICY.run_bound_usd
"""The projected spend that stops the study early: one run bound under the dollar cap."""


class BenchRefusedError(RuntimeError):
    pass


class ServerUnreachable(RuntimeError):
    """The model provider failed (connection, auth, or rate limit). Not an arm result."""


class AuthRejected(RuntimeError):
    """Jev refused the API key. The live run stops. The message never includes the key."""


_AUTH_ERROR = re.compile(r"\b401\b|unauthorized|invalid api key", re.IGNORECASE)
PREFLIGHT_TIMEOUT_S = 180.0


@dataclass(frozen=True)
class Setup:
    """What differs between the paid run and the dry run: the agent binary, the envs, and the secret."""

    agent: Callable[[Item, str], Sequence[str]]
    """The command that stands in for `claude`, per item and arm."""
    server: Sequence[str]
    server_env: Mapping[str, str]
    base_env: Mapping[str, str]
    """The agent's whole environment (`arms.agent_env` of the caller's)."""
    secret: str
    """Scrubbed from every kept artifact."""
    timeout_s: float = arms.RUN_TIMEOUT_S
    flags: Callable[[Item, str, Path], Sequence[str]] | None = None
    """Argv after the agent binary. None uses the Claude command; the Pi arm supplies its own."""
    trace: Callable[[Iterable[str]], stream.Trace] | None = None
    """Stream parser. None uses the Claude stream-json parser."""
    cross_check: Callable[[Sequence[Mapping[str, Any]], stream.Trace], None] | None = None
    """Proxy-vs-stream check for a B or C run. None uses the Claude check."""

    def __post_init__(self) -> None:
        if not self.secret:
            raise ValueError("Setup.secret must be the non-empty key to scrub")


def schedule(items: Sequence[Item], seed: int = SEED) -> list[tuple[Item, tuple[str, str, str]]]:
    """Seeded item order, and a seeded permutation of A, B, C within each item."""
    rng = random.Random(seed)
    order = list(items)
    rng.shuffle(order)
    arms_order = list(ARMS)
    planned: list[tuple[Item, tuple[str, str, str]]] = []
    for item in order:
        rng.shuffle(arms_order)
        planned.append((item, (arms_order[0], arms_order[1], arms_order[2])))
    return planned


def mcp_config(arm: str, jev_log: Path, setup: Setup) -> dict[str, Any]:
    """P8's harness server in every arm; B and C add the Jev server behind the bench proxy.

    The jev entry is eager and direct (`lifecycle`/`directTools`/`toolPrefix`, as the installer's
    pi entry): the published tools sit in the model's initial tool list instead of behind the
    adapter's lazy gateway, which recorded runs show the model never walks on its own (ADR-0036).
    """
    config = arms.mcp_config("A", jev_log=jev_log, server_env={})
    if arm in ("B", "C"):
        config["mcpServers"]["jev"] = {
            "type": "stdio",
            "command": sys.executable,
            "args": ["-m", "evals.bench.proxy", str(jev_log), "--", *setup.server],
            "env": dict(setup.server_env),
            "lifecycle": "eager",
            "directTools": True,
            "toolPrefix": "none",
        }
    return config


def jev_auth_error(calls: Sequence[Mapping[str, Any]]) -> bool:
    """True when an errored Jev call says the key was rejected. The key itself is not read."""
    return any(
        bool(call.get("is_error")) and _AUTH_ERROR.search(str(call.get("text") or "")) is not None for call in calls
    )


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


def run_one(item: Item, arm: str, setup: Setup, book: SpendLedger, out: Path) -> dict[str, Any]:
    run_id = f"{item.id}.{arm}"
    run_dir = out / run_id
    jev_log = run_dir / "jev-calls.jsonl"
    stop: RuntimeError | None = None
    text, addendum = prompt.render(item), prompt.addendum(arm)
    command = AgentCommand(
        argv=lambda config: [
            *setup.agent(item, arm),
            *(
                setup.flags(item, arm, config)
                if setup.flags is not None
                else arms.claude_command("claude", text, config, addendum)[1:]
            ),
        ],
        timeout_s=setup.timeout_s,
    )
    with run_agent(
        command,
        mcp_config=mcp_config(arm, jev_log, setup),
        base_env=setup.base_env,
        secret=setup.secret,
        run_dir=run_dir,
        parse=setup.trace,
    ) as run:

        def build() -> dict[str, Any]:
            nonlocal stop
            trace, status = run.trace, run.status
            # Claude reports MCP server status in its init event; Pi's stream does not, so the connection
            # check only applies when the trace names servers. Pi relies on the use gate and the proxy log.
            jev_down = arm in ("B", "C") and trace.mcp_servers and trace.mcp_servers.get("jev") != "connected"
            if status == "ok" and jev_down:
                status = f"failed: jev server {trace.mcp_servers.get('jev')}"
            calls = _read_jsonl(jev_log)
            use: str | None = None
            timings: list[spans.CallTiming] | None = None
            cross_check = None
            server_error = trace.result_field("server_error")
            if isinstance(server_error, str):
                status = f"server_failure: {server_error}"
                stop = ServerUnreachable(server_error)
            elif arm in ("B", "C"):
                use = gate.use_gate(calls) if arm == "C" else gate.automatic_note(calls)
                stderr_log = jev_log.with_suffix(".stderr")
                server_spans = (
                    spans.parse_spans(stderr_log.read_text(encoding="utf-8").splitlines())
                    if stderr_log.exists()
                    else []
                )
                timings = spans.attribute(calls, server_spans)
                try:
                    (setup.cross_check or gate.cross_check)(calls, trace)
                except gate.HarnessMismatchError as mismatch:
                    cross_check, stop = str(mismatch), mismatch
                wrong = gate.wrong_models(calls)
                if wrong and stop is None:
                    stop = gate.WrongModelError(f"{run_id}: Jev answered as {', '.join(wrong)}, not {arms.JEV_MODEL}")
                if jev_auth_error(calls) and stop is None:
                    status = "blocked: Jev rejected the API key"
                    stop = AuthRejected("Jev rejected the API key")
            parsed, parse_error = answer.parse(item, trace.result_field("result"))
            # C's missing Jev answer fails the run. B's "did not call Jev" does not.
            forced_miss = arm == "C" and use is not None
            correct = status == "ok" and stop is None and not forced_miss and answer.is_correct(item, parsed)
            raw_agent = trace.result_field("total_cost_usd")
            agent_flag = trace.result_field("agent_cost_reported")
            # No result (a timeout) may have spent the run bound. Pi can finish with usage and no cost
            # object: that agent spend is not measured, and it is not charged the bound.
            if trace.result is None or (agent_flag is None and raw_agent is None):
                cost = book.cost_of(None, calls)
                agent_reported = False
                agent_usd_value: float | None = cost.agent_usd
            elif agent_flag is False:
                cost = book.cost_of(0.0, calls)
                agent_reported = False
                agent_usd_value = None
            else:
                cost = book.cost_of(float(raw_agent or 0), calls)
                agent_reported = True
                agent_usd_value = float(raw_agent or 0)
            usage = trace.result_field("usage")
            return _bench_record(
                run_id,
                item,
                arm,
                run,
                {
                    "status": status,
                    "answer": list(parsed) if isinstance(parsed, tuple) else parsed,
                    "parse_error": parse_error,
                    "gate": use,
                    "correct": correct,
                    "cross_check": cross_check,
                    "server_failure": isinstance(server_error, str),
                    "agent_cost_usd": agent_usd_value,
                    "jev_cost_usd": cost.jev_usd,
                    "cost_usd": cost.total_usd if agent_usd_value is not None else cost.jev_usd,
                    "agent_cost_reported": agent_reported,
                    "usage": usage if isinstance(usage, dict) else {"reported": False},
                    "jev_calls": calls,
                    "timings": None if timings is None else [asdict(timing) for timing in timings],
                },
            )

        def failed(error: Exception) -> dict[str, Any]:
            return failed_record(run_id, item, arm, run, book.policy.run_bound_usd, error)

        record = write_record(run_dir / "result.json", build, failed)
    if stop is not None:
        raise stop
    return record


_RECORD_KEYS = (
    "run_id",
    "item",
    "tool",
    "family",
    "arm",
    "status",
    "answer",
    "parse_error",
    "gate",
    "correct",
    "cross_check",
    "server_failure",
    "wall_s",
    "duration_ms",
    "agent_model",
    "agent_cost_usd",
    "jev_cost_usd",
    "cost_usd",
    "agent_cost_reported",
    "num_turns",
    "tool_counts",
    "mcp_servers",
    "usage",
    "jev_calls",
    "timings",
)


def _bench_record(
    run_id: str, item: Item, arm: str, run: AgentRunResult, measured: Mapping[str, Any]
) -> dict[str, Any]:
    """The success record's keys. Failure fills `measured` and leaves the trace fields here."""
    trace = run.trace
    values: dict[str, Any] = {
        "run_id": run_id,
        "item": item.id,
        "tool": item.tool,
        "family": item.family,
        "arm": arm,
        "wall_s": run.wall_s,
        "duration_ms": trace.result_field("duration_ms"),
        "agent_model": trace.model,
        "num_turns": trace.result_field("num_turns"),
        "tool_counts": dict(trace.tool_counts),
        "mcp_servers": trace.mcp_servers,
    }
    values.update(measured)
    return record_row(_RECORD_KEYS, values)


def failed_record(
    run_id: str, item: Item, arm: str, run: AgentRunResult, run_bound_usd: float, error: Exception
) -> dict[str, Any]:
    """The record of a run whose post-processing raised: failed, unscored, and charged the run bound.

    Keys are the success record's (`_bench_record`). The study runner books the bound. Its gate stays
    None: a crash here is not a use-gate miss.
    """
    return _bench_record(
        run_id,
        item,
        arm,
        run,
        {
            "status": f"failed: post-processing raised {type(error).__name__}: {error}",
            "answer": None,
            "parse_error": None,
            "gate": None,
            "correct": False,
            "cross_check": None,
            "server_failure": False,
            "agent_cost_usd": run_bound_usd,
            "jev_cost_usd": 0.0,
            "cost_usd": run_bound_usd,
            "agent_cost_reported": False,
            "usage": {"reported": False},
            "jev_calls": [],
            "timings": None,
        },
    )


def load_records(out: Path) -> list[dict[str, Any]]:
    return [json.loads(path.read_text(encoding="utf-8")) for path in sorted(out.glob("*/result.json"))]


def recorded_model(out: Path) -> str | None:
    path = out / "model.txt"
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8").strip()
    return text or None


def _prior_stop(out: Path) -> str | None:
    """A connection error or an auth rejection already booked. Resume must not continue."""
    for record in load_records(out):
        if record.get("server_failure"):
            return f"stopped: provider failure on {record['run_id']}; not an arm result"
        if str(record.get("status", "")).startswith("blocked:"):
            return "blocked: Jev rejected the API key"
    return None


def recorded_thinking(out: Path) -> str | None:
    path = out / "thinking.txt"
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8").strip()
    return text or None


def write_report(out: Path, *, stop: str, sample: bool, labeled_items: int | None = None) -> Path:
    """The results view over whatever complete triplets `out` holds (`evals.bench.chart`)."""
    path = out / "report.html"
    records = load_records(out)
    path.write_text(
        chart.render(
            records,
            sample=sample,
            stop=stop,
            labeled_items=labeled_items,
            model_id=recorded_model(out),
            thinking=recorded_thinking(out),
        ),
        encoding="utf-8",
    )
    return path


def publish(out: Path, *, stop: str, labeled_items: int) -> tuple[Path, Path]:
    """The deliverable chart and numbers table, citing the run records. Raw transcripts stay gitignored."""
    records = load_records(out)
    model_id = recorded_model(out)
    thinking = recorded_thinking(out)
    reports = arms.REPO_ROOT / "evals" / "reports"
    chart_path = reports / "bench150.html"
    table_path = reports / "bench150.md"
    chart_path.write_text(
        chart.render(
            records, sample=False, stop=stop, labeled_items=labeled_items, model_id=model_id, thinking=thinking
        ),
        encoding="utf-8",
    )
    table_path.write_text(
        chart.numbers_md(records, labeled_items=labeled_items, stop=stop, model_id=model_id, thinking=thinking),
        encoding="utf-8",
    )
    return chart_path, table_path


def _stop_reason(run_id: str, error: BaseException) -> str | None:
    """Auth and provider failures are booked, then returned. They are not arm results."""
    if isinstance(error, AuthRejected):
        return "blocked: Jev rejected the API key"
    if isinstance(error, ServerUnreachable):
        return f"stopped: provider failure on {run_id}; not an arm result"
    return None


def run_bench(items: Sequence[Item], setup: Setup, out: Path, *, seed: int = SEED) -> str:
    """Run every triplet not yet recorded, within the caps; returns why it stopped."""
    if prior := _prior_stop(out):
        return prior
    book = SpendLedger.load(out / "ledger.json", POLICY)
    plan = schedule(items, seed)
    total_runs = TRIPLET * len(plan)
    for item, order in plan:
        for arm in order:
            run_id = f"{item.id}.{arm}"
            run_dir = out / run_id
            if agent_started(run_dir):
                book_outcome(book, run_id, run_dir / "result.json")
    for item, order in plan:
        records = load_records(out)
        triplets = analysis.complete_triplets(records)
        first = [triplets[i.id] for i, _ in plan if i.id in triplets][:EARLY_PAIRS]
        if len(first) == EARLY_PAIRS:
            jev_cost = {str(record["run_id"]): float(record.get("jev_cost_usd") or 0) for record in records}
            reason = analysis.early_stop(
                first, total_runs=total_runs, jev_cost=jev_cost, budget=COST_STOP_USD, max_failed=COMPLIANCE_STOP
            )
            if reason is not None:
                return f"{reason}; needs a decision before more runs"
        if item.id in triplets:
            continue
        if not book.can_start(TRIPLET):
            return f"stopped before {item.id}: {book.blocker(TRIPLET)}"
        for arm in order:
            run_id = f"{item.id}.{arm}"
            if run_id in book.runs:
                continue
            sys.stderr.write(f"run {run_id} (spent ${book.spent:.2f})\n")
            outcome = run_recorded(
                book,
                run_id,
                out / run_id / "result.json",
                lambda item=item, arm=arm: run_one(item, arm, setup, book, out),
                cost_key="jev_cost_usd",
                file_cost=(gate.HarnessMismatchError, gate.WrongModelError),
                file_default=0.0,
                stop=lambda error, run_id=run_id: _stop_reason(run_id, error),
            )
            if isinstance(outcome, str):
                return outcome
    return f"all {len(plan)} triplets recorded"


def main(argv: Sequence[str] | None = None, environ: Mapping[str, str] = os.environ) -> int:
    parser = argparse.ArgumentParser(prog="python -m evals.bench.run", description=__doc__)
    parser.add_argument("--seed", type=int, default=SEED)
    parser.add_argument("--agent", choices=("claude", "pi"), default="claude")
    parser.add_argument("--allow-draft", action="store_true", help="run while labels are still draft")
    args = parser.parse_args(argv)
    try:
        stop = live(environ, OUT, seed=args.seed, agent=args.agent, allow_draft=args.allow_draft)
    except BenchRefusedError as refusal:
        sys.stderr.write(f"refused: {refusal}\n")
        return 2
    items = load_items()
    labeled = sum(item.status != "draft" for item in items)
    triplets = analysis.complete_triplets(load_records(OUT))
    summary = {
        "stop": stop,
        "labeled_items": labeled,
        "model": recorded_model(OUT),
        **analysis.headline(triplets),
        "per_tool": analysis.per_tool(triplets, {item.id: item for item in items}),
    }
    (OUT / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    write_report(OUT, stop=stop, sample=False, labeled_items=labeled)
    publish(OUT, stop=stop, labeled_items=labeled)
    sys.stderr.write(json.dumps(summary, indent=2) + "\n")
    return 0


def pi_preflight(binary: str, base_env: Mapping[str, str], secret: str) -> str:
    """One prompt on the bench model. Returns the model id Pi reported.

    A connection, auth, or rate-limit error raises ServerUnreachable. The detail is one short line
    and does not include the key.
    """
    from evals.bench import pi as pi_mod

    scratch = Path(tempfile.mkdtemp(prefix="jev-bench-preflight-"))
    try:
        text = "Reply with the single word ok."
        command = AgentCommand(
            argv=lambda config: pi_mod.pi_command(
                binary, text, config, prompt.BENCH_ADDENDUM, model=pi_mod.BENCH_MODEL
            ),
            timeout_s=PREFLIGHT_TIMEOUT_S,
        )
        with run_agent(
            command,
            mcp_config=arms.mcp_config("A", jev_log=scratch / "jev.jsonl", server_env={}),
            base_env=base_env,
            secret=secret,
            run_dir=scratch,
            parse=pi_mod.parse,
        ) as run:
            failure = run.trace.result_field("server_error")
            if isinstance(failure, str):
                raise ServerUnreachable(failure.splitlines()[0][:160])
            if run.status != "ok":
                raise ServerUnreachable("preflight produced no answer")
            return run.trace.model or pi_mod.BENCH_MODEL
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def live(
    environ: Mapping[str, str],
    out: Path,
    *,
    seed: int = SEED,
    items: Sequence[Item] | None = None,
    agent: str = "claude",
    allow_draft: bool = False,
) -> str:
    if environ.get(LIVE_FLAG) != "1":
        raise BenchRefusedError(f"the bench calls paid providers; set {LIVE_FLAG}=1 to run it")
    key = environ.get("TYPESAFE_API_KEY")
    if not key:
        raise BenchRefusedError("TYPESAFE_API_KEY is not set")
    items = load_items() if items is None else items
    unfrozen = [item.id for item in items if item.status != "frozen"]
    if unfrozen and not allow_draft:
        raise BenchRefusedError(f"{len(unfrozen)} of {len(items)} items are not frozen (first: {unfrozen[0]})")
    base_env = arms.agent_env(environ)
    agent_path = base_env["PATH"]
    server_env = {**arms.jev_env(api_key=key, path=agent_path), "JEV_MCP_LOG_LEVEL": "DEBUG"}
    if agent == "pi":
        from evals.bench import pi

        binary = shutil.which("pi", path=agent_path)
        if binary is None:
            raise BenchRefusedError("pi not found on PATH")
        if not pi.ADAPTER.is_file():
            raise BenchRefusedError(f"pi MCP adapter not found at {pi.ADAPTER}")
        try:
            seen = pi_preflight(binary, base_env, key)
        except ServerUnreachable as error:
            out.mkdir(parents=True, exist_ok=True)
            (out / "preflight.txt").write_text("failed\n", encoding="utf-8")
            return f"stopped: Pi preflight failed ({error}); not an arm result"
        out.mkdir(parents=True, exist_ok=True)
        (out / "model.txt").write_text(seen + "\n", encoding="utf-8")
        (out / "thinking.txt").write_text(pi.PI_THINKING + "\n", encoding="utf-8")
        setup = Setup(
            agent=lambda _item, _arm: [binary],
            server=arms.jev_command(),
            server_env=server_env,
            base_env=base_env,
            secret=key,
            flags=pi.flags,
            trace=pi.parse,
            cross_check=pi.cross_check,
        )
    else:
        claude = shutil.which("claude", path=agent_path)
        if claude is None:
            raise BenchRefusedError("claude not found on PATH")
        setup = Setup(
            agent=lambda _item, _arm: [claude],
            server=arms.jev_command(),
            server_env=server_env,
            base_env=base_env,
            secret=key,
        )
    return run_bench(items, setup, out, seed=seed)


if __name__ == "__main__":
    raise SystemExit(main())
