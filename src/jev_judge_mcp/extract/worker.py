"""The production `RegexExecutor`: a pool of killable worker processes (ADR-0004, ADR-0016).

A slot is one process (`python -m jev_judge_mcp.extract.worker`) running one pattern at a time, fed
length-prefixed pickles of plain values over its stdin and stdout, never the server's stdout. Each
pattern arrives with one absolute deadline, taken before queue admission and spent across admission,
worker start, IPC, matching, and the reply — no stage resets the clock (ADR-0016). On the deadline,
or on cancellation (ADR-0011), that slot's process is killed and a fresh one is started on the next
demand; other slots keep running. Admission is bounded: a waiter count at `queue_bound` is
`Saturated` at once, and a pattern the pool cannot admit before its deadline times out unrun.

Matching uses the standard library's `re`, a backtracking matcher like V8's Irregexp: patterns that
time out in the reference also time out here. The `regex` module short-circuits some of them
(`(a+)+$` finishes at once), which would turn the reference's `invalid_pattern` into a result
(ADR-0018).
"""

import os
import pickle
import signal
import subprocess
import sys
from time import monotonic
from typing import Final

import anyio
from anyio.abc import Process
from anyio.streams.buffered import BufferedByteReceiveStream

from jev_judge_mcp.extract.dialect import Translated
from jev_judge_mcp.extract.executor import (
    Invalid,
    Matches,
    MatchResult,
    Saturated,
    Timeout,
    Unavailable,
    match_all,
)
from jev_judge_mcp.limits import EXTRACT, ExtractCaps

_START_TIMEOUT_S: Final = 30.0
_SELF_DEADLINE_GRACE_S: Final = 1.0


class _WorkerFailed(Exception):
    def __init__(self, cause: Unavailable) -> None:
        self.cause = cause


def main() -> None:
    """The worker process: answer one job at a time until stdin closes.

    Each job also arms SIGALRM, whose default action ends the process: if the server dies without
    killing a worker stuck in a pattern, the worker still ends shortly after the deadline. `re`
    never returns to the interpreter mid-match, so only a signal's default action can stop it.
    """
    signal.signal(signal.SIGINT, signal.SIG_IGN)  # the server owns shutdown; it kills its workers
    signal.signal(signal.SIGALRM, signal.SIG_DFL)
    requests, replies = sys.stdin.buffer, sys.stdout.buffer
    replies.write(_READY)
    replies.flush()
    while len(header := requests.read(_HEADER)) == _HEADER:
        job = pickle.loads(requests.read(int.from_bytes(header)))  # noqa: S301 - our parent
        source, flags, units, deadline, max_candidates, max_units = job
        signal.setitimer(signal.ITIMER_REAL, deadline + _SELF_DEADLINE_GRACE_S)
        matches = match_all(Translated(source, flags), units, max_candidates, max_units)
        signal.setitimer(signal.ITIMER_REAL, 0)
        reply = pickle.dumps((matches.candidates, matches.truncated, matches.too_long))
        replies.write(len(reply).to_bytes(_HEADER) + reply)
        replies.flush()


_READY: Final = b"R"
_HEADER: Final = 4


class _Slot:
    def __init__(self, process: Process) -> None:
        assert process.stdin is not None and process.stdout is not None
        self.process = process
        self.requests = process.stdin
        self.replies = BufferedByteReceiveStream(process.stdout)

    async def kill(self) -> None:
        with anyio.CancelScope(shield=True):
            try:
                self.process.kill()
            except ProcessLookupError:
                pass  # already reaped (a worker can die on a bad pattern before kill lands); the goal holds
            await self.process.aclose()


class ProcessRegexExecutor:
    """At most `size` patterns run at once, each in its own killable process.

    The caller's deadline is a whole-request budget (ADR-0016): admission, worker start, IPC, and
    matching all spend it. `queue_bound` caps how many patterns may wait for a slot. Each job carries
    the candidate caps from `caps` (`limits.EXTRACT`), so the worker holds no copy of them.
    """

    def __init__(self, size: int | None = None, queue_bound: int | None = None, caps: ExtractCaps = EXTRACT) -> None:
        self._caps = caps
        self.size = size or min(8, os.cpu_count() or 1)
        self.queue_bound = queue_bound if queue_bound is not None else 8 * self.size
        self._limit = anyio.Semaphore(self.size)
        self._waiting = 0
        self._idle: list[_Slot] = []

    async def find(self, pattern: Translated, text: str, *, deadline: float) -> MatchResult:
        """Match `pattern` over unit-space `text`; every stage spends what remains of `deadline`."""
        if self._waiting >= self.queue_bound:
            return Saturated()
        self._waiting += 1
        try:
            with anyio.fail_after(max(0.0, deadline - monotonic())):
                await self._limit.acquire()
        except TimeoutError:
            return Timeout()
        finally:
            self._waiting -= 1
        try:
            return await self._find(pattern, text, deadline)
        except TimeoutError:
            return Timeout()
        except _WorkerFailed as failed:
            return Invalid(failed.cause)
        finally:
            self._limit.release()

    async def _find(self, pattern: Translated, text: str, deadline: float) -> Matches:
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise TimeoutError
        idle = self._idle.pop() if self._idle else None
        if idle is not None:
            try:
                return await self._attempt(idle, pattern, text, deadline)
            except _WorkerFailed:
                # An idle pipe can be dead (a worker an OOM kill took between jobs): nothing ran,
                # so one fresh worker may still answer inside the same budget.
                pass
        return await self._attempt(await self._start(deadline - monotonic()), pattern, text, deadline)

    async def _attempt(self, slot: _Slot, pattern: Translated, text: str, deadline: float) -> Matches:
        healthy = False
        try:
            matches = await self._run(slot, pattern, text, deadline - monotonic())
            healthy = True
            return matches
        finally:
            if healthy:
                self._idle.append(slot)
            else:
                await slot.kill()

    async def _run(self, slot: _Slot, pattern: Translated, units: str, budget: float) -> Matches:
        if budget <= 0:
            raise TimeoutError
        caps = self._caps
        job = pickle.dumps(
            (pattern.source, pattern.flags, units, budget, caps.candidates_per_field, caps.candidate_units)
        )
        try:
            with anyio.fail_after(budget):
                await slot.requests.send(len(job).to_bytes(_HEADER) + job)
                size = int.from_bytes(await slot.replies.receive_exactly(_HEADER))
                reply = await slot.replies.receive_exactly(size)
        except TimeoutError:
            raise  # an OSError, but the deadline's, not a crash
        except (
            anyio.IncompleteRead,
            anyio.EndOfStream,
            anyio.BrokenResourceError,
            anyio.ClosedResourceError,
            OSError,
        ):
            # `ClosedResourceError` too: a slot whose stream was closed is a dead slot, whether the
            # process died with its pipe open (broken) or was reaped first (closed).
            raise _WorkerFailed(Unavailable.NO_RESULT) from None
        candidates, truncated, too_long = pickle.loads(reply)  # noqa: S301 - our own child
        return Matches(candidates, truncated, too_long)

    async def _start(self, budget: float = _START_TIMEOUT_S) -> _Slot:
        """A ready worker. Raises `TimeoutError` if `budget` runs out first, `_WorkerFailed` on a crash."""
        try:
            # Shielded: a cancel (or a spent budget) delivered during the spawn is deferred until
            # the first checkpoint below, inside the armed try — so the process is never left
            # without a handle to kill (ADR-0011).
            with anyio.CancelScope(shield=True):
                process = await anyio.open_process(
                    [sys.executable, "-m", __name__], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=None
                )
        except OSError:
            # EMFILE/ENOMEM: nowhere to run the pattern at all. This is the `NOT_STARTED` refusal's
            # failure class — not an escape past the executor contract into a protocol error.
            raise _WorkerFailed(Unavailable.NOT_STARTED) from None
        slot = _Slot(process)
        try:
            with anyio.fail_after(budget):
                ready = await slot.replies.receive_exactly(len(_READY))
        except TimeoutError:
            # The budget ran out before the worker signalled readiness — a deadline, not a crash.
            await slot.kill()
            raise
        except anyio.get_cancelled_exc_class():
            # `_find` does not own the slot until start returns, so a cancel here must reap it (ADR-0011).
            await slot.kill()
            raise
        except (anyio.IncompleteRead, anyio.EndOfStream):
            await slot.kill()
            raise _WorkerFailed(Unavailable.NOT_STARTED) from None
        if ready != _READY:
            await slot.kill()
            raise _WorkerFailed(Unavailable.NOT_STARTED)
        return slot

    async def warm(self) -> None:
        """Start one worker ahead of demand."""
        if not self._idle:
            self._idle.append(await self._start())

    async def aclose(self) -> None:
        idle, self._idle = self._idle, []
        for slot in idle:
            await slot.kill()


if __name__ == "__main__":
    main()
