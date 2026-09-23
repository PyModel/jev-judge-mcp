"""Paid-run spend: an immutable `SpendPolicy` and the mutable `SpendLedger` that accounts against it.

The policy holds the caps and the Jev price; the ledger holds what was spent, persisted, and is the only
place a run's cost is computed (`cost_of`). Each paid harness keeps its own policy and its own ledger
file, so their caps are spent independently. A run is recorded whatever its outcome and never retried.
"""

import fcntl
import json
import math
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

JEV_PUBLISHED_USD_PER_MTOK_INPUT = 0.042
"""TypeSafe's published list price (docs/jev_docs/models.md). Output tokens are free. Not a measured bill."""


@dataclass(frozen=True)
class SpendPolicy:
    max_runs: int
    max_usd: float
    jev_usd_per_mtok_input: float
    run_bound_usd: float
    """One run's worst case: what a run is charged when it reports no agent cost or never finishes."""
    max_batch_usd: float | None = None
    """Worst case of one batch (the runs one `can_start` admits); a batch starts only if it fits under
    what is left of `max_usd`. None keeps no headroom: only what is already spent is checked."""


@dataclass(frozen=True)
class RunCost:
    agent_usd: float
    """The agent CLI's reported `total_cost_usd` (list price), or the run bound when it reported none."""
    jev_usd: float
    """The policy's Jev price times the input tokens the Jev calls reported."""
    agent_reported: bool = True

    @property
    def total_usd(self) -> float:
        return self.agent_usd + self.jev_usd


@dataclass
class SpendLedger:
    """What was spent. An exclusive flock is held for this process; the kernel drops it on exit (ADR-0032)."""

    policy: SpendPolicy
    path: Path
    runs: dict[str, float] = field(default_factory=dict[str, float])

    @classmethod
    def load(cls, path: Path, policy: SpendPolicy) -> "SpendLedger":
        _acquire(path)
        runs: dict[str, float] = json.loads(path.read_text(encoding="utf-8"))["runs"] if path.exists() else {}
        return cls(policy, path, runs)

    @property
    def spent(self) -> float:
        return sum(self.runs.values())

    def cost_of(self, agent_cost_usd: float | None, jev_calls: Sequence[Mapping[str, Any]]) -> RunCost:
        """A run with no usable agent cost may have spent its whole budget, so it is charged the run bound.

        Missing, non-finite (NaN, inf), and negative reports are not usable. They are clamped to the
        bound with `agent_reported` false, never stored, so a NaN spend cannot fail the dollar cap open.
        """
        tokens = sum(int(call.get("input_tokens") or 0) for call in jev_calls)
        jev = tokens * self.policy.jev_usd_per_mtok_input / 1_000_000
        if agent_cost_usd is None or not math.isfinite(agent_cost_usd) or agent_cost_usd < 0:
            return RunCost(self.policy.run_bound_usd, jev, agent_reported=False)
        return RunCost(float(agent_cost_usd), jev)

    def record_unfinished(self, run_id: str) -> None:
        """Book a run that raised before it produced a cost, at the run bound, so a resume never pays twice."""
        self.record(run_id, self.policy.run_bound_usd)

    def blocker(self, runs: int = 1) -> str | None:
        """Why a batch of `runs` more runs may not start, or None when it may."""
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
        if run_id in self.runs:
            raise ValueError(f"{run_id} already ran; runs are never retried")
        if not math.isfinite(cost_usd) or cost_usd < 0:
            raise ValueError(f"{run_id} cost must be finite and >= 0")
        updated = {**self.runs, run_id: cost_usd}
        _persist(self.path, updated)
        self.runs[run_id] = cost_usd


def agent_started(run_dir: Path) -> bool:
    """True when the agent left `stream.jsonl` or `claude.stderr`. Resume books only those runs."""
    return (run_dir / "stream.jsonl").is_file() or (run_dir / "claude.stderr").is_file()


def book_outcome(ledger: SpendLedger, run_id: str, result_path: Path) -> None:
    """Book `run_id` once: a finite non-negative `cost_usd` from `result_path`, otherwise the run bound.

    A second call returns without writing, so resume can call this before `can_start`. A non-finite
    file cost, including NaN, is not booked; the run bound is, and the ledger text has no NaN token.
    """
    if run_id in ledger.runs:
        return
    cost = _file_cost(result_path)
    ledger.record(run_id, ledger.policy.run_bound_usd if cost is None else cost)


def _file_cost(result_path: Path) -> float | None:
    if not result_path.is_file():
        return None
    try:
        payload = json.loads(result_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    cost = cast(dict[str, Any], payload).get("cost_usd")
    if isinstance(cost, bool) or not isinstance(cost, int | float):
        return None
    if not math.isfinite(cost) or cost < 0:
        return None
    return float(cost)


# One flock fd per ledger path for this process. A second `load` here must not deadlock on Linux,
# where flock is per open-file description. The fd stays open so the kernel, not a sentinel, owns
# the lock (ADR-0032: this program is POSIX-only; there is no second platform check).
_LOCKS: dict[str, int] = {}


def _acquire(path: Path) -> None:
    """Exclusive non-blocking flock for this process.

    Released by the kernel when the process dies, including kill -9. Another process that
    already holds it gets an immediate error, not a wait. The lock file's contents are never
    read and never mean "held". POSIX-only (ADR-0032).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.parent / f"{path.name}.lock"
    key = os.path.realpath(lock_path)
    if key in _LOCKS:
        return
    fd = os.open(key, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        raise RuntimeError(f"{path}: another process holds this ledger") from None
    except BaseException:
        os.close(fd)
        raise
    _LOCKS[key] = fd


def _persist(path: Path, runs: dict[str, float]) -> None:
    """Write the ledger via a temp file and `os.replace`, while this process holds the flock."""
    _acquire(path)
    payload = json.dumps({"runs": runs}, indent=2, allow_nan=False) + "\n"
    temporary = path.parent / f".{path.name}.{os.getpid()}.tmp"
    temporary.write_text(payload, encoding="utf-8")
    try:
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
