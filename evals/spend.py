"""Account for paid runs under a process-owned flock and a shared in-process ledger."""

import fcntl
import json
import math
import os
import threading
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from types import MappingProxyType
from typing import Any, cast

from evals.artifacts import write_json

JEV_PUBLISHED_USD_PER_MTOK_INPUT = 0.042
"""TypeSafe's published list price (docs/jev_docs/models.md). Output tokens are free."""


@dataclass(frozen=True)
class SpendPolicy:
    max_runs: int
    max_usd: float
    jev_usd_per_mtok_input: float
    run_bound_usd: float
    max_batch_usd: float | None = None


@dataclass(frozen=True)
class RunCost:
    agent_usd: float
    jev_usd: float
    agent_reported: bool = True

    @property
    def total_usd(self) -> float:
        return self.agent_usd + self.jev_usd


def amount(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    try:
        number = float(value)
    except OverflowError:
        return None
    return number if math.isfinite(number) and number >= 0 else None


def booked_amount(value: object, run_bound_usd: float) -> float:
    cost = amount(value)
    return run_bound_usd if cost is None else cost


@dataclass
class _History:
    fd: int
    runs: dict[str, float]
    cap: float


_histories: dict[Path, _History] = {}
_mutex = threading.RLock()


def _after_fork() -> None:
    global _mutex
    for state in _histories.values():
        os.close(state.fd)
    _histories.clear()
    _mutex = threading.RLock()


os.register_at_fork(after_in_child=_after_fork)


def _read_history(path: Path) -> tuple[dict[str, float], float | None]:
    runs: dict[str, float] = {}
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return runs, None
    payload: object = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError(f"{path}: expected a ledger object")
    entries = cast(dict[str, object], payload)
    stored = entries.get("runs")
    if not isinstance(stored, dict):
        raise ValueError(f"{path}: expected a runs object")
    for key, value in cast(dict[str, object], stored).items():
        cost = amount(value)
        if not key.strip() or cost is None:
            raise ValueError(f"{path}: invalid run identifier or cost")
        runs[key] = cost
    limit = None
    if "max_usd" in entries:
        limit = amount(entries["max_usd"])
        if limit is None:
            raise ValueError(f"{path}: stored max_usd must be a finite number >= 0")
    return runs, limit


def reconciliation_notes(
    out: Path, records: Mapping[Path, dict[str, Any]], incomplete: Sequence[Path], *, cost_key: str
) -> list[str]:
    """Compare published results with the last atomic ledger snapshot, without taking its write lock."""
    try:
        runs, _ = _read_history(out / "ledger.json")
    except (ValueError, OSError, RecursionError):
        return ["unreadable spend ledger"]
    amounts = {path.parent.name: amount(row.get(cost_key)) for path, row in records.items()}
    missing = runs.keys() - amounts.keys() - {path.parent.name for path in incomplete}
    notes = ["booked without result: " + ", ".join(sorted(missing))] if missing else []
    for run_id, cost in sorted(amounts.items()):
        if cost is not None and run_id in runs and runs[run_id] != cost:
            notes.append(f"cost discrepancy: {run_id} (${runs[run_id]:g} booked, ${cost:g} in result)")
    return notes


def _open_history(path: Path, cap: float) -> _History:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path.with_name(path.name + ".lock"), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError(f"{path}: another process holds this ledger") from None
        runs, limit = _read_history(path)
        if limit is not None:
            if cap < limit:
                write_json(path, {"max_usd": cap, "runs": runs})
            cap = min(cap, limit)
        return _History(fd, runs, cap)
    except BaseException:
        os.close(fd)
        raise


class SpendLedger:
    def __init__(self, policy: SpendPolicy, path: Path) -> None:
        self.path = path.resolve()
        self._policy = policy
        with _mutex:
            state = _histories.get(self.path)
            if state is None:
                state = _open_history(self.path, policy.max_usd)
                _histories[self.path] = state
            if policy.max_usd < state.cap:
                write_json(self.path, {"max_usd": policy.max_usd, "runs": state.runs})
                state.cap = policy.max_usd
            self._state = state

    @classmethod
    def load(cls, path: Path, policy: SpendPolicy) -> "SpendLedger":
        return cls(policy, path)

    @property
    def policy(self) -> SpendPolicy:
        return replace(self._policy, max_usd=self._state.cap)

    @property
    def runs(self) -> Mapping[str, float]:
        return MappingProxyType(self._state.runs)

    @property
    def spent(self) -> float:
        with _mutex:
            return sum(self.runs.values())

    def cost_of(self, agent_cost_usd: float | None, jev_calls: Sequence[Mapping[str, Any]]) -> RunCost:
        policy = self.policy
        tokens = 0
        unknown = False
        for call in jev_calls:
            if call.get("refused") is True:
                continue
            count = call.get("input_tokens")
            if type(count) is int and count >= 0 and count <= 2**63 - 1:
                tokens += count
            else:
                unknown = True
        jev = tokens * policy.jev_usd_per_mtok_input / 1_000_000
        if unknown:
            jev = max(jev, policy.run_bound_usd)
        agent = amount(agent_cost_usd)
        return RunCost(policy.run_bound_usd if agent is None else agent, jev, agent is not None)

    def record_unfinished(self, run_id: str) -> None:
        self.record(run_id, self.policy.run_bound_usd)

    def blocker(self, runs: int = 1) -> str | None:
        with _mutex:
            policy = self.policy
            if len(self.runs) + runs > policy.max_runs:
                return f"run cap: {len(self.runs)} of {policy.max_runs} runs done"
            worst = policy.max_batch_usd or 0.0
            if self.spent + worst > policy.max_usd:
                return f"dollar cap: {self.spent:.2f} spent + {worst:.2f} worst case > {policy.max_usd:.2f}"
            return None

    def can_start(self, runs: int = 1) -> bool:
        return self.blocker(runs) is None

    def record(self, run_id: str, cost_usd: float) -> None:
        with _mutex:
            if _histories.get(self.path) is not self._state:
                raise RuntimeError("reopen the ledger in the child process")
            if run_id in self.runs:
                raise ValueError(f"{run_id} already ran; runs are never retried")
            if not run_id.strip() or amount(cost_usd) is None:
                raise ValueError("run ID must be nonempty and cost must be finite and >= 0")
            write_json(self.path, {"max_usd": self.policy.max_usd, "runs": {**self.runs, run_id: cost_usd}})
            self._state.runs[run_id] = cost_usd


def agent_started(run_dir: Path) -> bool:
    return any((run_dir / name).is_file() for name in ("result.json", "stream.jsonl", "claude.stderr"))


def book_outcome(ledger: SpendLedger, run_id: str, result_path: Path, *, cost_key: str = "cost_usd") -> None:
    """Book the selected cost once, reserving the run bound for missing or invalid evidence."""
    with _mutex:
        if run_id in ledger.runs:
            return
        try:
            row: object = json.loads(result_path.read_text(encoding="utf-8"))
        except (ValueError, OSError, RecursionError):
            row = None
        raw = cast(dict[str, Any], row).get(cost_key) if isinstance(row, dict) else None
        ledger.record(run_id, booked_amount(raw, ledger.policy.run_bound_usd))
