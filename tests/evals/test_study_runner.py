"""The paid-study protocol, without an agent and without a paid run."""

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from evals.spend import SpendLedger, SpendPolicy
from evals.study_runner import record_row, run_recorded, write_record

BOUND = 3.5


class Mismatch(RuntimeError):
    pass


def _book(tmp_path: Path) -> SpendLedger:
    policy = SpendPolicy(max_runs=4, max_usd=25.0, jev_usd_per_mtok_input=0.042, run_bound_usd=BOUND)
    return SpendLedger.load(tmp_path / "ledger.json", policy)


def test_record_row_keeps_the_success_key_order_and_rejects_drift() -> None:
    assert list(record_row(("b", "a"), {"a": 1, "b": 2})) == ["b", "a"]
    with pytest.raises(KeyError, match="drifted"):
        record_row(("a",), {"a": 1, "b": 2})
    with pytest.raises(KeyError, match="drifted"):
        record_row(("a", "b"), {"a": 1})


def test_write_record_writes_success_outside_the_handler_and_failed_on_exception(tmp_path: Path) -> None:
    path = tmp_path / "result.json"
    record = write_record(path, lambda: {"cost_usd": 1, "arm": "A"}, lambda _error: {"no": True})
    assert record == {"cost_usd": 1, "arm": "A"}
    assert path.read_text(encoding="utf-8") == '{\n  "cost_usd": 1,\n  "arm": "A"\n}\n'

    def build() -> dict[str, Any]:
        raise RuntimeError("grading broke")

    with pytest.raises(RuntimeError, match="grading broke"):
        write_record(path, build, lambda error: {"status": f"{type(error).__name__}: {error}"})
    assert json.loads(path.read_text(encoding="utf-8")) == {"status": "RuntimeError: grading broke"}

    missing = tmp_path / "missing.json"
    with pytest.raises(KeyboardInterrupt):
        write_record(missing, _interrupt, lambda _error: {"wrote": True})
    assert not missing.exists()


def test_success_books_the_returned_cost_not_the_file(tmp_path: Path) -> None:
    book = _book(tmp_path)
    got = run_recorded(book, "run-a", tmp_path / "absent.json", lambda: {"cost_usd": 1.25})
    assert isinstance(got, dict)
    assert got["cost_usd"] == 1.25
    assert book.runs == {"run-a": 1.25}


@pytest.mark.parametrize("exc", [KeyboardInterrupt, SystemExit])
def test_an_operator_interrupt_books_the_file_or_the_bound(tmp_path: Path, exc: type[BaseException]) -> None:
    book = _book(tmp_path)
    with pytest.raises(exc):
        run_recorded(book, "bare", tmp_path / "absent.json", lambda: _raise(exc))
    priced = tmp_path / "result.json"
    priced.write_text(json.dumps({"cost_usd": 0.2}) + "\n", encoding="utf-8")
    with pytest.raises(exc):
        run_recorded(book, "priced", priced, lambda: _raise(exc))
    assert book.runs == {"bare": BOUND, "priced": 0.2}


def test_system_exit_keeps_its_code(tmp_path: Path) -> None:
    book = _book(tmp_path)

    def execute() -> dict[str, Any]:
        raise SystemExit(2)

    with pytest.raises(SystemExit) as caught:
        run_recorded(book, "run-a", tmp_path / "absent.json", execute)
    assert caught.value.code == 2
    assert book.runs == {"run-a": BOUND}


def test_an_ordinary_exception_books_the_bound_even_when_the_file_is_cheaper(tmp_path: Path) -> None:
    book = _book(tmp_path)
    path = tmp_path / "result.json"

    def execute() -> dict[str, Any]:
        path.write_text(json.dumps({"cost_usd": 0.5}) + "\n", encoding="utf-8")
        raise RuntimeError("grading broke")

    with pytest.raises(RuntimeError, match="grading broke"):
        run_recorded(book, "run-a", path, execute)
    assert book.runs == {"run-a": BOUND}


def test_a_named_file_cost_error_books_the_file_and_a_missing_file_does_not_book_the_bound(tmp_path: Path) -> None:
    book = _book(tmp_path)
    path = tmp_path / "result.json"

    def priced() -> dict[str, Any]:
        path.write_text(json.dumps({"cost_usd": 0.5}) + "\n", encoding="utf-8")
        raise Mismatch("proxy")

    with pytest.raises(Mismatch):
        run_recorded(book, "priced", path, priced, file_cost=(Mismatch,))
    assert book.runs == {"priced": 0.5}

    with pytest.raises(FileNotFoundError):
        run_recorded(book, "missing", tmp_path / "absent.json", _mismatch, file_cost=(Mismatch,))
    assert book.runs == {"priced": 0.5}


def test_success_can_book_jev_cost_instead_of_the_agent_total(tmp_path: Path) -> None:
    book = _book(tmp_path)
    got = run_recorded(
        book,
        "run-a",
        tmp_path / "absent.json",
        lambda: {"cost_usd": 9.0, "jev_cost_usd": 0.4},
        cost_key="jev_cost_usd",
    )
    assert isinstance(got, dict)
    assert got["jev_cost_usd"] == 0.4
    assert book.runs == {"run-a": 0.4}


def test_a_stop_books_the_named_file_cost_and_returns_its_reason(tmp_path: Path) -> None:
    book = _book(tmp_path)
    path = tmp_path / "result.json"
    path.write_text(json.dumps({"cost_usd": 9.0, "jev_cost_usd": 0.4}) + "\n", encoding="utf-8")

    def stop(error: BaseException) -> str | None:
        return "blocked: Jev rejected the API key" if isinstance(error, Mismatch) else None

    got = run_recorded(
        book,
        "run-a",
        path,
        _mismatch,
        cost_key="jev_cost_usd",
        file_default=0.0,
        stop=stop,
    )
    assert got == "blocked: Jev rejected the API key"
    assert book.runs == {"run-a": 0.4}


def test_a_missing_stopped_cost_books_the_file_default_and_not_the_bound(tmp_path: Path) -> None:
    book = _book(tmp_path)
    path = tmp_path / "result.json"
    path.write_text("{}\n", encoding="utf-8")
    with pytest.raises(Mismatch):
        run_recorded(book, "run-a", path, _mismatch, file_cost=(Mismatch,), file_default=0.0)
    assert book.runs == {"run-a": 0.0}


def test_cancellation_is_never_booked_even_when_named_as_file_cost(tmp_path: Path) -> None:
    book = _book(tmp_path)
    path = tmp_path / "result.json"
    path.write_text(json.dumps({"cost_usd": 0.5}) + "\n", encoding="utf-8")
    with pytest.raises(asyncio.CancelledError):
        run_recorded(book, "run-a", path, _cancel, file_cost=(asyncio.CancelledError,))
    assert book.runs == {}


def test_the_booking_handler_is_not_copied_into_either_study() -> None:
    root = Path(__file__).resolve().parents[2]
    for relative in ("evals/ab/run.py", "evals/bench/run.py"):
        text = (root / relative).read_text(encoding="utf-8")
        assert "KeyboardInterrupt" not in text
        assert "record_unfinished" not in text
        assert "run_recorded(" in text
        assert "write_record(" in text
        assert "record_row(" in text


def _raise(exc: type[BaseException]) -> dict[str, Any]:
    raise exc


def _interrupt() -> dict[str, Any]:
    raise KeyboardInterrupt


def _mismatch() -> dict[str, Any]:
    raise Mismatch("proxy")


def _cancel() -> dict[str, Any]:
    raise asyncio.CancelledError
