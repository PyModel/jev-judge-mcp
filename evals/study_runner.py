"""The paid-study protocol for the A/B outcome study and the bench (ADR-0045).

One module owns run → record → write → failed record → book. Each study keeps its own row
contents. A failed record is the success record's keys (`record_row`); it does not keep a second list.

Booking happens once, in `run_recorded`:
- `InfrastructureStop` books an explicit charge or recovers the outcome of a started agent.
- `KeyboardInterrupt` and `SystemExit` call `book_outcome` (the result file's finite selected cost, else the run bound).
- `stop` may turn an `Exception` into a returned reason after booking `cost_key` from the written file.
  The bench uses this for `AuthRejected` and `ServerUnreachable`.
- Other `Exception` types named in `file_cost` book that same field and re-raise. The bench names
  `HarnessMismatchError` and `WrongModelError`.
- Any other `Exception` calls `record_unfinished` (the run bound), and does not read the file.
- `CancelledError` is not an `Exception`. It propagates unbooked.

Success books `record[cost_key]`. The A/B study uses `cost_usd`. The bench uses `jev_cost_usd`, because its
dollar cap does not include agent spend. Unverifiable charges reserve the run bound.
"""

from collections.abc import Callable, Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

from evals.artifacts import read_result, write_json
from evals.spend import SpendLedger, agent_started, amount, book_outcome, booked_amount


class InfrastructureStop(BaseException):
    """Stop the study without synthesizing an agent failure record."""

    def __init__(self, reason: str, incurred_usd: float | None = None) -> None:
        super().__init__(reason)
        self.incurred_usd = incurred_usd


def recover_attempts(
    ledger: SpendLedger,
    out: Path,
    run_ids: Iterable[str],
    records: Mapping[Path, Mapping[str, Any]],
    *,
    cost_key: str,
) -> str | None:
    """Book started attempts once and flag existing charges below the required amount."""
    bound = ledger.policy.run_bound_usd
    prior = None
    for run_id in dict.fromkeys((*run_ids, *ledger.runs)):
        run_dir = out / run_id
        existing = ledger.runs.get(run_id)
        if existing is None and not agent_started(run_dir):
            continue
        path = run_dir / "result.json"
        row = records.get(path)
        measured = None
        if row is not None:
            measured = amount(row.get(cost_key))
            charge = bound if measured is None else measured
        elif existing is None:
            raw = read_result(path)
            charge = (
                max(bound, booked_amount(raw.get(cost_key), bound)) if raw and raw.get("run_id") == run_id else bound
            )
        else:
            continue
        if existing is None:
            try:
                ledger.record(run_id, charge)
            except ValueError:
                if ledger.runs.get(run_id) != charge:
                    raise
        elif existing < charge and prior is None:
            kind = "unfinished reserve" if measured is None else "recorded cost"
            prior = f"stopped: {run_id} booked below its {kind} of ${charge:g}"
    return prior


def record_row(keys: Sequence[str], values: Mapping[str, Any]) -> dict[str, Any]:
    """A record with exactly `keys`, in that order.

    The failed record is this row filled with failure values, so its keys are the success record's keys.
    A missing or extra key is a programming error: the two rows have drifted.
    """
    missing = [key for key in keys if key not in values]
    extra = [key for key in values if key not in keys]
    if missing or extra:
        raise KeyError(f"record keys drifted: missing {missing}, extra {extra}")
    return {key: values[key] for key in keys}


def write_record(
    result_path: Path,
    build: Callable[[], dict[str, Any]],
    failed: Callable[[Exception], dict[str, Any]],
) -> dict[str, Any]:
    """Write `result.json`. On `Exception`, write the failed record and re-raise.

    The success write sits outside the handler, as each study wrote it. `KeyboardInterrupt`,
    `SystemExit`, and `CancelledError` propagate with nothing written here.
    """
    try:
        record = build()
    except Exception as error:
        failed_row = failed(error)
        write_json(result_path, failed_row)
        raise
    write_json(result_path, record)
    return record


def run_recorded(
    ledger: SpendLedger,
    run_id: str,
    result_path: Path,
    execute: Callable[[], Mapping[str, Any]],
    *,
    cost_key: str = "cost_usd",
    file_cost: tuple[type[BaseException], ...] = (),
    stop: Callable[[BaseException], str | None] | None = None,
) -> Mapping[str, Any] | str:
    """Run one recorded attempt and book it. See the module docstring for which exception books what.

    A string return is a stop reason. The caller returns it instead of continuing the study.
    """
    try:
        record = execute()
    except InfrastructureStop as error:
        if run_id not in ledger.runs:
            if (charge := amount(error.incurred_usd)) is not None:
                ledger.record(run_id, charge)
            elif agent_started(result_path.parent):
                book_outcome(ledger, run_id, result_path, cost_key=cost_key)
        return f"stopped: isolation infrastructure: {error}"
    except (KeyboardInterrupt, SystemExit):
        book_outcome(ledger, run_id, result_path, cost_key=cost_key)
        raise
    except Exception as error:
        reason = stop(error) if stop is not None else None
        if reason is not None or (file_cost and isinstance(error, file_cost)):
            book_outcome(ledger, run_id, result_path, cost_key=cost_key)
            if reason is not None:
                return reason
        else:
            ledger.record_unfinished(run_id)
        raise
    ledger.record(run_id, booked_amount(record.get(cost_key), ledger.policy.run_bound_usd))
    return record
