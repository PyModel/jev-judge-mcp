"""Spend ledger booking without either harness loop. No agent, no paid run."""

import json
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from evals.spend import SpendLedger, SpendPolicy, book_outcome

BOUND = 3.5


def _policy() -> SpendPolicy:
    return SpendPolicy(max_runs=10, max_usd=25.0, jev_usd_per_mtok_input=0.042, run_bound_usd=BOUND)


def _ledger(tmp_path: Path) -> SpendLedger:
    return SpendLedger.load(tmp_path / "ledger.json", _policy())


def test_book_outcome_uses_a_finite_file_cost_and_writes_no_nan(tmp_path: Path) -> None:
    book = _ledger(tmp_path)
    finite = tmp_path / "finite.json"
    finite.write_text(json.dumps({"cost_usd": 1.25}) + "\n", encoding="utf-8")
    book_outcome(book, "priced", finite)
    book_outcome(book, "priced", finite)
    missing = tmp_path / "missing.json"
    book_outcome(book, "killed", missing)
    poisoned = tmp_path / "poisoned.json"
    poisoned.write_text('{"cost_usd": NaN}\n', encoding="utf-8")
    book_outcome(book, "nan-file", poisoned)
    assert book.runs == {"priced": 1.25, "killed": BOUND, "nan-file": BOUND}
    raw = (tmp_path / "ledger.json").read_bytes()
    assert b"NaN" not in raw
    assert b"Infinity" not in raw


def test_cost_of_clamps_non_finite_and_negative_agent_cost(tmp_path: Path) -> None:
    book = _ledger(tmp_path)
    calls = [{"input_tokens": 1_000_000}]
    cases = (("nan", float("nan")), ("inf", float("inf")), ("neginf", float("-inf")), ("neg", -5.0))
    for name, bad in cases:
        cost = book.cost_of(bad, calls)
        assert (cost.agent_usd, cost.agent_reported) == (BOUND, False)
        assert cost.jev_usd == 0.042
        book.record(name, cost.total_usd)
    raw = (tmp_path / "ledger.json").read_bytes()
    assert b"NaN" not in raw
    assert b"Infinity" not in raw
    assert b"-5" not in raw
    assert raw[:1] == b"{" and raw.rstrip(b"\n").endswith(b"}")


def test_record_rejects_non_finite_and_negative_cost(tmp_path: Path) -> None:
    book = _ledger(tmp_path)
    book.record("ok", 1.25)
    before = (tmp_path / "ledger.json").read_bytes()
    for bad in (float("nan"), float("inf"), float("-inf"), -5.0):
        with pytest.raises(ValueError, match="finite"):
            book.record("bad", bad)
    assert (tmp_path / "ledger.json").read_bytes() == before
    assert b"NaN" not in before
    assert b"Infinity" not in before


def test_a_second_load_in_this_process_records(tmp_path: Path) -> None:
    path = tmp_path / "ledger.json"
    first = SpendLedger.load(path, _policy())
    first.record("a", 1.0)
    second = SpendLedger.load(path, _policy())
    assert second.runs["a"] == 1.0
    second.record("b", 2.0)
    assert SpendLedger.load(path, _policy()).runs == {"a": 1.0, "b": 2.0}


def test_a_crash_before_replace_leaves_the_previous_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    book = _ledger(tmp_path)
    book.record("kept", 1.25)
    previous = (tmp_path / "ledger.json").read_bytes()

    def crash(_src: str | os.PathLike[str], _dst: str | os.PathLike[str]) -> None:
        raise RuntimeError("killed before replace")

    monkeypatch.setattr(os, "replace", crash)
    with pytest.raises(RuntimeError, match="killed before replace"):
        book.record("lost", 2.0)
    assert book.runs == {"kept": 1.25}
    raw = (tmp_path / "ledger.json").read_bytes()
    assert raw == previous
    assert b'"kept"' in raw
    assert b"lost" not in raw


_HOLD = """
import sys, time
from pathlib import Path
from evals.spend import SpendLedger, SpendPolicy
path = Path(sys.argv[1])
policy = SpendPolicy(max_runs=10, max_usd=25.0, jev_usd_per_mtok_input=0.042, run_bound_usd=3.5)
book = SpendLedger.load(path, policy)
book.record("held", 1.0)
print("ready", flush=True)
time.sleep(30)
"""

_NEXT = """
import sys
from pathlib import Path
from evals.spend import SpendLedger, SpendPolicy
path = Path(sys.argv[1])
policy = SpendPolicy(max_runs=10, max_usd=25.0, jev_usd_per_mtok_input=0.042, run_bound_usd=3.5)
book = SpendLedger.load(path, policy)
assert book.runs["held"] == 1.0
book.record("next", 2.0)
print("done", flush=True)
"""


def _spawn(script: str, path: Path) -> subprocess.Popen[str]:
    env = os.environ.copy()
    root = str(Path(__file__).resolve().parents[2])
    env["PYTHONPATH"] = root + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    return subprocess.Popen(
        [sys.executable, "-c", script, str(path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )


def _ready(holder: subprocess.Popen[str]) -> None:
    assert holder.stdout is not None and holder.stderr is not None
    ready = holder.stdout.readline()
    if ready.strip() != "ready":
        raise AssertionError(holder.stderr.read() or ready)


def _stop(holder: subprocess.Popen[str]) -> None:
    if holder.poll() is None:
        os.kill(holder.pid, signal.SIGKILL)
    holder.wait(timeout=5)


def test_a_held_ledger_raises_instead_of_blocking(tmp_path: Path) -> None:
    path = tmp_path / "ledger.json"
    holder = _spawn(_HOLD, path)
    errors: list[BaseException] = []

    def attempt() -> None:
        try:
            SpendLedger.load(path, _policy())
        except BaseException as exc:
            errors.append(exc)

    loader = threading.Thread(target=attempt)
    try:
        _ready(holder)
        started = time.monotonic()
        loader.start()
        loader.join(1)
        assert not loader.is_alive(), "load blocked while another process held the ledger"
        assert time.monotonic() - started < 1
    finally:
        _stop(holder)
        loader.join(timeout=5)
    assert len(errors) == 1
    assert isinstance(errors[0], RuntimeError)
    assert str(path) in str(errors[0])
    assert "another process" in str(errors[0])


def test_kill_minus_nine_lets_the_next_process_load_and_record(tmp_path: Path) -> None:
    path = tmp_path / "ledger.json"
    holder = _spawn(_HOLD, path)
    _ready(holder)
    _stop(holder)
    second = _spawn(_NEXT, path)
    assert second.stdout is not None and second.stderr is not None
    assert second.wait(timeout=5) == 0, second.stderr.read()
    assert second.stdout.read().strip() == "done"
    raw = path.read_bytes()
    assert b'"held"' in raw and b'"next"' in raw
    assert b"NaN" not in raw and b"Infinity" not in raw
