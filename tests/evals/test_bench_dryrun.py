"""The bench's offline dry run: `evals.bench.run` end to end with a stub agent and a loopback provider.

Each run spawns the stub (`tests/support/bench_agent.py`) in place of `claude`, which spawns the bench
proxy in front of the real `python -m jev_judge_mcp`, pointed at `tests/support/bench_provider.py` on
127.0.0.1. The items are the two hand-written rows in `data/bench-dryrun.jsonl`, never the 150-item file.
"""

import http.client
import json
import os
import sys
import threading
import urllib.parse
from collections.abc import Callable, Iterator, Sequence
from itertools import pairwise
from pathlib import Path
from typing import Any

import pytest

from evals.ab import arms
from evals.bench import gate, run, spans
from evals.bench.items import Item, load_items
from evals.bench.ledger import POLICY
from evals.bench.proxy import BENCH_REQUEST_CAP
from evals.spend import SpendLedger
from tests.evals.booking_cases import (
    bench_loop,
    cancelled_error_does_not_book,
    operator_interrupt_books_the_bound,
    seeded_nan_books_the_bound,
    seeded_result_books_the_file_cost,
)
from tests.support.bench_provider import API_KEY, Loopback

REPO = Path(__file__).resolve().parents[2]
AGENT = REPO / "tests" / "support" / "bench_agent.py"
ITEMS = load_items(Path(__file__).resolve().parent / "data" / "bench-dryrun.jsonl")
ANSWERS = {"dry-screen-01": "clean", "dry-verify-01": "verified"}

Plan = dict[str, Any]
Planner = Callable[[Item, str], Plan]


def default_plan(item: Item, arm: str) -> Plan:
    """C is the forced arm and calls Jev. B is automatic and, unless a test says otherwise, does not."""
    calls = [{"tool": item.tool, "arguments": dict(item.input)}] if arm == "C" else []
    return {"calls": calls, "answer": f"The material is plain.\n{json.dumps({'answer': ANSWERS[item.id]})}"}


def no_calls(item: Item, arm: str) -> Plan:
    return {**default_plan(item, arm), "calls": []}


def setup(tmp: Path, loopback: Loopback, plan: Planner = default_plan) -> run.Setup:
    def agent(item: Item, arm: str) -> list[str]:
        path = tmp / f"plan-{item.id}.{arm}.json"
        path.write_text(json.dumps(plan(item, arm)), encoding="utf-8")
        return [sys.executable, str(AGENT), str(path)]

    env = {
        **loopback.env(),
        "JEV_MCP_MODEL": "jev-1.13.0",
        "JEV_MCP_LOG_LEVEL": "DEBUG",
        "PYTHONPATH": str(REPO),
        "PATH": os.environ.get("PATH", ""),
    }
    return run.Setup(
        agent=agent,
        server=[sys.executable, "-m", "jev_judge_mcp"],
        server_env=env,
        base_env=arms.agent_env(os.environ),
        secret=API_KEY,
        timeout_s=120,
    )


@pytest.fixture
def loopback() -> Iterator[Loopback]:
    with Loopback() as endpoint:
        yield endpoint


def records(out: Path) -> dict[str, dict[str, Any]]:
    return {r["run_id"]: r for r in run.load_records(out)}


def test_dry_run_scores_both_pairs_and_writes_the_sample_report(tmp_path: Path, loopback: Loopback) -> None:
    out = tmp_path / "out"
    stop = run.run_bench(ITEMS, setup(tmp_path, loopback), out)
    assert stop == "all 2 triplets recorded"
    got = records(out)
    assert sorted(got) == [
        "dry-screen-01.A",
        "dry-screen-01.B",
        "dry-screen-01.C",
        "dry-verify-01.A",
        "dry-verify-01.B",
        "dry-verify-01.C",
    ]
    for record in got.values():
        assert record["status"] == "ok" and record["correct"]
        assert record["answer"] == ANSWERS[record["item"]]
    for item in ITEMS:
        forced = got[f"{item.id}.C"]
        assert forced["gate"] is None and forced["cross_check"] is None
        assert [c["tool"] for c in forced["jev_calls"]] == [item.tool]
        call = forced["jev_calls"][0]
        assert call["model"] == "jev-1.13.0" and call["arguments"] == dict(item.input)
        assert json.loads(call["text"])["tool"] == item.tool
        (timing,) = forced["timings"]
        assert timing["provider_ms"] is not None and timing["provider_ms"] > 0
        assert timing["round_trip_ms"] >= timing["provider_ms"] + timing["server_ms"] - 0.1
        automatic = got[f"{item.id}.B"]
        assert automatic["gate"] == "did not call Jev" and automatic["correct"] and automatic["jev_calls"] == []
        assert got[f"{item.id}.A"]["jev_calls"] == [] and got[f"{item.id}.A"]["gate"] is None
        assert got[f"{item.id}.A"]["timings"] is None
    assert API_KEY not in "".join(p.read_text(encoding="utf-8") for p in out.rglob("*") if p.is_file())
    assert set(SpendLedger.load(out / "ledger.json", POLICY).runs) == set(got)
    assert run.run_bench(ITEMS, setup(tmp_path, loopback), out) == "all 2 triplets recorded", "resume runs nothing"
    report = run.write_report(out, stop=stop, sample=True).read_text(encoding="utf-8")
    assert "SAMPLE: dry-run stub data" in report and "not a bench result" in report
    for text in ("A direct", "B automatic", "C forced", "did not call Jev", "Accuracy: share of items correct",
                 "items per minute", "Time consumed: total wall time", "unit: seconds", "100.0%", "<svg"):  # fmt: skip
        assert text in report
    assert "<script" not in report and "http" not in report.split("<body>")[1]


def _gated(tmp_path: Path, loopback: Loopback, plan: Planner) -> dict[str, dict[str, Any]]:
    run.run_bench(ITEMS[:1], setup(tmp_path, loopback, plan), tmp_path / "out")
    return records(tmp_path / "out")


def test_gate_fails_a_forced_run_that_never_called_jev(tmp_path: Path, loopback: Loopback) -> None:
    got = _gated(tmp_path, loopback, no_calls)
    forced = got["dry-screen-01.C"]
    assert forced["gate"] == "no Jev call" and forced["answer"] == "clean" and not forced["correct"]
    automatic = got["dry-screen-01.B"]
    assert automatic["gate"] == "did not call Jev" and automatic["answer"] == "clean" and automatic["correct"]
    assert got["dry-screen-01.A"]["correct"] and got["dry-screen-01.A"]["gate"] is None


def test_gate_fails_a_forced_run_whose_only_jev_calls_errored(tmp_path: Path) -> None:
    with Loopback(status=500) as failing:
        got = _gated(tmp_path, failing, default_plan)
    forced = got["dry-screen-01.C"]
    assert forced["gate"].startswith("no successful Jev call") and not forced["correct"]
    assert [c["is_error"] for c in forced["jev_calls"]] == [True] and forced["cross_check"] is None


def test_wrong_model_fails_the_gate_and_stops_the_study(tmp_path: Path) -> None:
    with Loopback(model="jev-latest") as unpinned, pytest.raises(gate.WrongModelError, match="jev-latest"):
        run.run_bench(ITEMS, setup(tmp_path, unpinned), tmp_path / "out")
    got = records(tmp_path / "out")
    callers = [record for record in got.values() if record["jev_calls"]]
    assert len(callers) == 1 and callers[0]["arm"] == "C"
    assert callers[0]["gate"] == "no Jev answer from jev-1.13.0" and not callers[0]["correct"]
    assert callers[0]["run_id"] in SpendLedger.load(tmp_path / "out" / "ledger.json", POLICY).runs, (
        "recorded, never retried"
    )
    other = "dry-verify-01" if callers[0]["item"] == "dry-screen-01" else "dry-screen-01"
    assert not any(run_id.startswith(other) for run_id in got)


def test_the_26th_call_is_refused_and_the_run_still_used_jev(tmp_path: Path, loopback: Loopback) -> None:
    def plan(item: Item, arm: str) -> Plan:
        base = default_plan(item, arm)
        return {**base, "calls": base["calls"] * (BENCH_REQUEST_CAP + 1)}

    forced = _gated(tmp_path, loopback, plan)["dry-screen-01.C"]
    assert [c["refused"] for c in sorted(forced["jev_calls"], key=lambda c: c["seq"])] == [False] * 25 + [True]
    assert loopback.requests == BENCH_REQUEST_CAP
    assert forced["gate"] is None and forced["correct"] and forced["cross_check"] is None
    assert forced["timings"] is not None and len(forced["timings"]) == BENCH_REQUEST_CAP


def test_parallel_calls_are_unattributed(tmp_path: Path, loopback: Loopback) -> None:
    def plan(item: Item, arm: str) -> Plan:
        base = default_plan(item, arm)
        return {**base, "calls": base["calls"] * 4, "parallel": True}

    forced = _gated(tmp_path, loopback, plan)["dry-screen-01.C"]
    assert len(forced["jev_calls"]) == 4 and forced["gate"] is None
    calls = sorted(forced["jev_calls"], key=lambda c: c["t0"])
    if any(later["t0"] < earlier["t1"] for earlier, later in pairwise(calls)):
        assert forced["timings"] is None
    else:
        assert forced["timings"] is not None and len(forced["timings"]) == 4


def test_a_run_whose_post_processing_raises_is_booked_at_the_run_bound(
    tmp_path: Path, loopback: Loopback, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(_lines: object) -> list[object]:
        raise RuntimeError(f"span parse broke {API_KEY}")

    launched: list[str] = []

    def plan(item: Item, arm: str) -> Plan:
        launched.append(f"{item.id}.{arm}")
        return default_plan(item, arm)

    def pinned(items: Sequence[Item], seed: int = 0) -> list[tuple[Item, tuple[str, str, str]]]:
        del seed
        return [(items[0], ("A", "B", "C"))]

    original = spans.parse_spans
    monkeypatch.setattr(run, "schedule", pinned)
    monkeypatch.setattr(spans, "parse_spans", broken)
    out = tmp_path / "out"
    with pytest.raises(RuntimeError, match="span parse broke"):
        run.run_bench(ITEMS[:1], setup(tmp_path, loopback, plan), out)
    book = SpendLedger.load(out / "ledger.json", POLICY)
    assert book.runs["dry-screen-01.B"] == POLICY.run_bound_usd, "paid, so booked and never retried"
    assert API_KEY not in "".join(p.read_text(encoding="utf-8") for p in out.rglob("*") if p.is_file())
    failed = records(out)["dry-screen-01.B"]
    assert failed["status"].startswith("failed: post-processing raised RuntimeError: span parse broke")
    assert failed["cost_usd"] == failed["agent_cost_usd"] == POLICY.run_bound_usd and not failed["correct"]
    assert failed["agent_cost_reported"] is False and failed["gate"] is None
    kept = (out / "dry-screen-01.B" / "result.json").read_text(encoding="utf-8")
    monkeypatch.setattr(spans, "parse_spans", original)
    assert run.run_bench(ITEMS[:1], setup(tmp_path, loopback, plan), out) == "all 1 triplets recorded"
    assert launched.count("dry-screen-01.B") == 1, "a resume never launches a booked run again"
    assert "dry-screen-01.A" in launched and "dry-screen-01.C" in launched
    assert (out / "dry-screen-01.B" / "result.json").read_text(encoding="utf-8") == kept
    assert "post-processing raised" in run.write_report(out, stop="x", sample=True).read_text(encoding="utf-8")


def test_a_stream_proxy_mismatch_stops_the_study(tmp_path: Path, loopback: Loopback) -> None:
    def plan(item: Item, arm: str) -> Plan:
        return {**default_plan(item, arm), "hide": [item.tool]}

    with pytest.raises(gate.HarnessMismatchError):
        run.run_bench(ITEMS[:1], setup(tmp_path, loopback, plan), tmp_path / "out")
    mismatched = [record for record in records(tmp_path / "out").values() if record["cross_check"]]
    assert len(mismatched) == 1 and mismatched[0]["arm"] == "C" and not mismatched[0]["correct"]


def test_an_unparseable_final_answer_is_incorrect(tmp_path: Path, loopback: Loopback) -> None:
    def plan(item: Item, arm: str) -> Plan:
        return {**default_plan(item, arm), "answer": "It is clean, I think."}

    got = _gated(tmp_path, loopback, plan)
    assert all(not r["correct"] and r["parse_error"] == "final line is not JSON" for r in got.values())


def test_the_ledger_stops_between_triplets(tmp_path: Path, loopback: Loopback) -> None:
    out = tmp_path / "out"
    book = SpendLedger.load(out / "ledger.json", POLICY)
    for n in range(POLICY.max_runs - 3):
        book.record(f"earlier-{n}", 0.0)
    stop = run.run_bench(ITEMS, setup(tmp_path, loopback), out)
    assert stop.startswith("stopped before") and "run cap" in stop
    got = records(out)
    assert (
        len(got) == 3
        and {r["arm"] for r in got.values()} == {"A", "B", "C"}
        and len({r["item"] for r in got.values()}) == 1
    )
    report = run.write_report(out, stop=stop, sample=True).read_text(encoding="utf-8")
    assert "run cap" in report


def test_the_compliance_stop_holds_on_resume(
    tmp_path: Path, loopback: Loopback, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(run, "EARLY_PAIRS", 1)
    monkeypatch.setattr(run, "COMPLIANCE_STOP", 1)
    out = tmp_path / "out"
    plan = no_calls
    stop = run.run_bench(ITEMS, setup(tmp_path, loopback, plan), out)
    assert stop.startswith("compliance stop: 1 of the first 1 C runs failed") and len(records(out)) == 3
    assert run.run_bench(ITEMS, setup(tmp_path, loopback, plan), out) == stop
    assert len(records(out)) == 3


class SpyLock:
    """A lock that counts how often it is held."""

    def __init__(self) -> None:
        self.inner = threading.Lock()
        self.entered = 0

    def __enter__(self) -> None:
        self.inner.acquire()
        self.entered += 1

    def __exit__(self, *exc: object) -> None:
        self.inner.release()


def test_the_loopback_counts_requests_under_its_lock(loopback: Loopback) -> None:
    """The handler runs on one thread per connection, so the counter is incremented under a lock."""
    spy = SpyLock()
    loopback.lock = spy
    url = urllib.parse.urlsplit(loopback.env()["JEV_API_BASE_URL"])
    body = json.dumps({"questions": {"q": {"type": "noul"}}})
    for _ in range(3):
        connection = http.client.HTTPConnection(url.netloc)
        connection.request("POST", url.path, body, {"Content-Type": "application/json"})
        assert connection.getresponse().status == 200
        connection.close()
    assert (loopback.requests, spy.entered) == (3, 3)


@pytest.mark.parametrize("exc", [KeyboardInterrupt, SystemExit])
def test_an_operator_interrupt_books_the_bound_and_resume_does_not_relaunch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, exc: type[BaseException]
) -> None:
    operator_interrupt_books_the_bound(bench_loop(tmp_path, monkeypatch), exc)


def test_a_cancelled_study_does_not_book(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cancelled_error_does_not_book(bench_loop(tmp_path, monkeypatch))


def test_resume_books_a_seeded_result_cost_not_the_bound(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seeded_result_books_the_file_cost(bench_loop(tmp_path, monkeypatch))


def test_resume_books_the_bound_when_the_seeded_cost_is_nan(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seeded_nan_books_the_bound(bench_loop(tmp_path, monkeypatch))
