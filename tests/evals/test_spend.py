"""Spend ledger booking without either harness loop. No agent, no paid run."""

import json
import os
import select
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
    assert {p.name for p in tmp_path.iterdir()} == {"ledger.json", "ledger.json.lock"}


_HOLD = """
import sys, time
from pathlib import Path
from evals.spend import SpendLedger, SpendPolicy
path = Path(sys.argv[1])
policy = SpendPolicy(max_runs=10, max_usd=25.0, jev_usd_per_mtok_input=0.042, run_bound_usd=3.5)
book = SpendLedger.load(path, policy)
book.record("held", 1.0)
sys.stdout.write("R")
sys.stdout.flush()
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


def _ready(holder: subprocess.Popen[str], *, timeout: float = 5) -> None:
    assert holder.stdout is not None
    if not select.select([holder.stdout], [], [], timeout)[0]:
        raise TimeoutError("ledger holder readiness deadline expired")
    marker = os.read(holder.stdout.fileno(), 1)
    if not marker:
        raise EOFError("ledger holder reached EOF before readiness")
    assert marker == b"R", "unexpected ledger holder readiness marker"


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
        if loader.ident is not None:
            loader.join(timeout=5)
    assert len(errors) == 1
    assert isinstance(errors[0], RuntimeError)
    assert str(path) in str(errors[0])
    assert "another process" in str(errors[0])


def test_kill_minus_nine_lets_the_next_process_load_and_record(tmp_path: Path) -> None:
    path = tmp_path / "ledger.json"
    holder = _spawn(_HOLD, path)
    try:
        _ready(holder)
    finally:
        _stop(holder)
    second = _spawn(_NEXT, path)
    assert second.stdout is not None and second.stderr is not None
    assert second.wait(timeout=5) == 0, second.stderr.read()
    assert second.stdout.read().strip() == "done"
    raw = path.read_bytes()
    assert b'"held"' in raw and b'"next"' in raw
    assert b"NaN" not in raw and b"Infinity" not in raw


@pytest.mark.parametrize(
    "payload",
    [
        {"runs": {"a": -2}},
        {"runs": {"a": True}},
        {"runs": {"a": float("inf")}},
        {"runs": {"a": "2"}},
        {"runs": {"": 0}},
        {"runs": []},
        [],
        {},
        {"runs": None},
        {"runs": {"a": 10**400}},
        {"runs": {"a": float("nan")}},
        {"runs": {}, "max_usd": None},
    ],
)
def test_scout_002_refuses_invalid_history_without_changing_it(tmp_path: Path, payload: object) -> None:
    path = tmp_path / "ledger.json"
    before = json.dumps(payload)
    path.write_text(before)
    with pytest.raises(ValueError):
        SpendLedger.load(path, _policy())
    assert path.read_text() == before


@pytest.mark.parametrize("tokens", [None, -1, False, "20", 0.5, 10**400])
def test_scout_002_unverifiable_usage_reserves_the_run_bound(tmp_path: Path, tokens: object) -> None:
    book = _ledger(tmp_path)
    assert book.cost_of(0.0, [{"input_tokens": tokens}]).jev_usd >= BOUND
    assert book.cost_of(0.0, [{"input_tokens": 0}]).jev_usd == 0


def test_scout_008_two_open_handles_share_charges_and_lowered_cap(tmp_path: Path) -> None:
    from dataclasses import replace

    first = _ledger(tmp_path)
    second = SpendLedger.load(first.path, replace(_policy(), max_usd=1.0))
    first.record("one", 0.5)
    second.record("two", 0.75)
    assert json.loads(first.path.read_text()) == {"runs": {"one": 0.5, "two": 0.75}, "max_usd": 1.0}
    assert not first.can_start()
    with pytest.raises(ValueError, match="already ran"):
        second.record("one", 0)


def test_scout_008_rejected_load_releases_lock_for_another_process(tmp_path: Path) -> None:
    path = tmp_path / "ledger.json"
    path.write_text("[]")
    with pytest.raises(ValueError):
        SpendLedger.load(path, _policy())
    path.write_text(json.dumps({"runs": {"held": 1.0}}))
    with _spawn(_NEXT, path) as second:
        _, error = second.communicate(timeout=5)
        assert second.returncode == 0, error
    assert json.loads(path.read_text())["runs"] == {"held": 1.0, "next": 2.0}


@pytest.mark.parametrize(
    ("script", "error", "reason"),
    [("pass", EOFError, "EOF"), ("import time; time.sleep(4)", TimeoutError, "readiness deadline")],
)
def test_holder_readiness_failures(tmp_path: Path, script: str, error: type[Exception], reason: str) -> None:
    holder = _spawn(script, tmp_path / "ledger.json")
    try:
        with pytest.raises(error, match=reason):
            _ready(holder, timeout=0.3)
    finally:
        _stop(holder)


def test_reconcile_published_results_and_missing_metering(tmp_path: Path) -> None:
    from evals.spend import reconciliation_notes

    rows = {tmp_path / "paid/result.json": {"cost_usd": 2}, tmp_path / "unknown/result.json": {}}
    assert reconciliation_notes(tmp_path, {}, [], cost_key="cost_usd") == []
    notes = reconciliation_notes(tmp_path, rows, [], cost_key="cost_usd")
    assert "missing spend ledger" in notes
    assert "result without booking: paid, unknown" in notes
    assert "unmeasurable result cost: unknown" in notes
    (tmp_path / "ledger.json").write_text('{"runs":{"paid":2,"other":1}}')
    assert reconciliation_notes(tmp_path, rows, [], cost_key="cost_usd") == [
        "booked without result: other",
        "result without booking: unknown",
        "unmeasurable result cost: unknown",
    ]


def test_ledger_preserves_published_charge_if_directory_sync_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import stat

    book = _ledger(tmp_path)
    sync = os.fsync

    def fail_directory(fd: int) -> None:
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            raise OSError("directory sync failed")
        sync(fd)

    monkeypatch.setattr(os, "fsync", fail_directory)
    with pytest.raises(OSError, match="directory sync failed"):
        book.record("published", 1)
    assert book.runs == json.loads(book.path.read_text())["runs"] == {"published": 1}
    with pytest.raises(ValueError, match="already ran"):
        book.record("published", 1)


def test_last_ledger_handle_releases_study(tmp_path: Path) -> None:
    first = _ledger(tmp_path)
    second = _ledger(tmp_path)
    first.record("held", 1)
    first.close()
    first.close()
    with pytest.raises(RuntimeError, match="closed"):
        first.record("invalid", 1)
    with _spawn(_NEXT, second.path) as other:
        assert "another process" in other.communicate(timeout=5)[1]
    second.close()
    with _spawn(_NEXT, second.path) as other:
        _, error = other.communicate(timeout=5)
        assert other.returncode == 0, error


def test_writer_failure_after_replace_keeps_shared_ledger_current(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from collections.abc import Mapping
    from typing import Any

    from evals import spend

    book = _ledger(tmp_path)
    peer = _ledger(tmp_path)
    publish = spend.write_json

    def fail_after_replace(path: Path, payload: Mapping[str, Any]) -> None:
        publish(path, payload)
        raise OSError("publication failed after replace")

    monkeypatch.setattr(spend, "write_json", fail_after_replace)
    with pytest.raises(OSError, match="failed after replace"):
        book.record("published", 1)
    assert peer.runs == json.loads(book.path.read_text())["runs"] == {"published": 1}


def test_spend_recovery_bounds_stored_history(tmp_path: Path) -> None:
    from evals.artifacts import RESULT_BYTES_MAX
    from evals.spend import reconciliation_notes

    history = tmp_path / "ledger.json"
    history.write_bytes(b'{"runs": {}}'.ljust(RESULT_BYTES_MAX + 1))
    with pytest.raises(OSError, match="exceeds"):
        _ledger(tmp_path)
    assert reconciliation_notes(tmp_path, {}, [], cost_key="cost_usd") == ["unreadable spend ledger"]


def test_spend_recovery_bounds_result_cost(tmp_path: Path) -> None:
    from evals.artifacts import RESULT_BYTES_MAX

    ledger = _ledger(tmp_path)
    result = tmp_path / "result.json"
    result.write_bytes(b'{"cost_usd": 0.25}'.ljust(RESULT_BYTES_MAX))
    book_outcome(ledger, "at-limit", result)
    with result.open("ab") as output:
        output.write(b" ")
    book_outcome(ledger, "oversized", result)
    assert ledger.runs == {"at-limit": 0.25, "oversized": BOUND}


def test_spend_recovery_requires_regular_history_at_its_own_path(tmp_path: Path) -> None:
    target = tmp_path / "copied.json"
    target.write_text('{"runs": {"previous": 1}}')
    (tmp_path / "ledger.json").symlink_to(target)
    with pytest.raises(OSError):
        _ledger(tmp_path)


def test_spend_recovery_keeps_last_readable_ledger_on_oversized_write(tmp_path: Path) -> None:
    from evals.artifacts import RESULT_BYTES_MAX

    book = _ledger(tmp_path)
    book.record("kept", 1)
    previous = book.path.read_bytes()
    with pytest.raises((ValueError, OSError), match="exceeds"):
        book.record("a" * RESULT_BYTES_MAX, 2)
    assert book.path.read_bytes() == previous
    assert book.runs == {"kept": 1}
