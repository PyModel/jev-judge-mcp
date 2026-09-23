"""Shared FIX-01 cases. Imported by the A/B and bench harness tests; not collected on its own.

Each case drives the harness's public `study` / `run_bench` through a `Loop`. `run_one` is replaced,
so nothing here launches an agent or spends money.
"""

import asyncio
import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from evals.ab import ledger, tasks
from evals.ab import run as ab_run
from evals.bench import run as bench_run
from evals.bench.items import Item, load_items
from evals.bench.ledger import POLICY
from evals.spend import SpendLedger

DONE = "all 1 pairs recorded"
SCRUB = "scrub-me"


@dataclass
class Loop:
    ids: list[str]
    bound: float
    out: Path
    go: Callable[[], str]
    install: Callable[[Callable[..., Any]], None]
    load: Callable[[], SpendLedger]
    seed: Callable[[str, float], None]
    rid_of: Callable[..., str]
    done: str


def assert_no_nan(path: Path) -> None:
    raw = path.read_bytes()
    assert b"NaN" not in raw
    assert b"Infinity" not in raw


def operator_interrupt_books_the_bound(loop: Loop, exc: type[BaseException]) -> None:
    seen: list[str] = []

    def run_one(*args: Any, **_kwargs: Any) -> None:
        seen.append(loop.rid_of(*args))
        raise exc()

    loop.install(run_one)
    with pytest.raises(exc):
        loop.go()
    assert seen == [loop.ids[0]]
    assert loop.load().runs == {loop.ids[0]: loop.bound}
    assert_no_nan(loop.out / "ledger.json")
    for _ in loop.ids[1:]:
        with pytest.raises(exc):
            loop.go()
    assert seen == loop.ids
    assert loop.load().runs == {run_id: loop.bound for run_id in loop.ids}
    assert loop.go() == loop.done
    assert seen == loop.ids, "resume does not relaunch a booked run"
    assert_no_nan(loop.out / "ledger.json")


def cancelled_error_does_not_book(loop: Loop) -> None:
    seen: list[str] = []

    def run_one(*args: Any, **_kwargs: Any) -> None:
        seen.append(loop.rid_of(*args))
        raise asyncio.CancelledError()

    loop.install(run_one)
    with pytest.raises(asyncio.CancelledError):
        loop.go()
    assert seen == [loop.ids[0]]
    assert loop.load().runs == {}
    with pytest.raises(asyncio.CancelledError):
        loop.go()
    assert seen == [loop.ids[0], loop.ids[0]], "an unbooked cancellation launches again"
    assert loop.load().runs == {}


def seeded_result_books_the_file_cost(loop: Loop) -> None:
    for run_id in loop.ids:
        loop.seed(run_id, 1.25)
    seen: list[str] = []

    def run_one(*_args: Any, **_kwargs: Any) -> None:
        seen.append("launched")
        raise AssertionError("relaunched")

    loop.install(run_one)
    assert loop.go() == loop.done
    assert seen == []
    assert loop.load().runs == {run_id: 1.25 for run_id in loop.ids}
    assert 1.25 != loop.bound
    assert_no_nan(loop.out / "ledger.json")


def seeded_nan_books_the_bound(loop: Loop) -> None:
    for run_id in loop.ids:
        loop.seed(run_id, float("nan"))
    seen: list[str] = []

    def run_one(*_args: Any, **_kwargs: Any) -> None:
        seen.append("launched")
        raise AssertionError("relaunched")

    loop.install(run_one)
    assert loop.go() == loop.done
    assert seen == []
    assert loop.load().runs == {run_id: loop.bound for run_id in loop.ids}
    assert_no_nan(loop.out / "ledger.json")


def ab_loop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Loop:
    task = tasks.load_task("j1-refund-window")
    setup = ab_run.Setup(
        agent="claude",
        binary=["unused"],
        server_env={},
        base_env={},
        secret=SCRUB,
        python3="python3",
        task_list=[task],
    )
    out = tmp_path / "out"
    plan = ab_run.schedule(setup.task_list, 1, seed=ab_run.SEED)
    ids = [ab_run.run_id(planned.id, arm, repeat) for planned, repeat, order in plan for arm in order]
    policy = ledger.POLICIES["claude"]

    def seed(run_id: str, cost: float) -> None:
        write_result(out, run_id, cost, bench_keys=False)

    def rid_of(task: tasks.Task, arm: str, repeat: int, *_rest: object) -> str:
        return ab_run.run_id(task.id, arm, repeat)

    return Loop(
        ids=ids,
        bound=policy.run_bound_usd,
        out=out,
        go=lambda: ab_run.study(setup, out, repeats=1),
        install=lambda fn: monkeypatch.setattr(ab_run, "run_one", fn),
        load=lambda: SpendLedger.load(out / "ledger.json", policy),
        seed=seed,
        rid_of=rid_of,
        done=DONE,
    )


def bench_loop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Loop:
    items = load_items(Path(__file__).resolve().parent / "data" / "bench-dryrun.jsonl")[:1]
    setup = bench_run.Setup(
        agent=lambda _item, _arm: ["unused"],
        server=[],
        server_env={},
        base_env={"PATH": "/usr/bin:/bin"},
        secret=SCRUB,
    )
    out = tmp_path / "out"
    plan = bench_run.schedule(items, seed=bench_run.SEED)
    ids = [f"{item.id}.{arm}" for item, order in plan for arm in order]

    def seed(run_id: str, cost: float) -> None:
        write_result(out, run_id, cost, bench_keys=True)

    def rid_of(item: Item, arm: str, *_rest: object) -> str:
        return f"{item.id}.{arm}"

    return Loop(
        ids=ids,
        bound=POLICY.run_bound_usd,
        out=out,
        go=lambda: bench_run.run_bench(items, setup, out),
        install=lambda fn: monkeypatch.setattr(bench_run, "run_one", fn),
        load=lambda: SpendLedger.load(out / "ledger.json", POLICY),
        seed=seed,
        rid_of=rid_of,
        done="all 1 triplets recorded",
    )


def write_result(out: Path, run_id: str, cost: float, *, bench_keys: bool) -> None:
    run_dir = out / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "stream.jsonl").write_text("", encoding="utf-8")
    payload: dict[str, Any] = {"cost_usd": cost}
    if bench_keys:
        item, arm = run_id.rsplit(".", 1)
        payload["item"] = item
        payload["arm"] = arm
    (run_dir / "result.json").write_text(json.dumps(payload) + "\n", encoding="utf-8")
