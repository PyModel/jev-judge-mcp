"""The paid-study protocol for the A/B outcome study and the bench (ADR-0045).

One module owns run → record → write → failed record → book. Each study keeps its own row
contents. A failed record is the success record's keys (`record_row`); it does not keep a second list.

Booking happens once, in `run_recorded`:
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

from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from evals.artifacts import write_json
from evals.spend import SpendLedger, book_outcome, booked_amount


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

    A string return is a stop reason (`stop`). The attempt is already booked. The caller returns it
    instead of continuing the study.
    """
    try:
        record = execute()
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
