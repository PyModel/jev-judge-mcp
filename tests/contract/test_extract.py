"""jev_extract hard invariants (ROADMAP P5) and the worker pool's isolation (ADR-0004, ADR-0011)."""

import contextlib
import json
import os
import pickle
import signal
import subprocess
import sys
import time
from collections.abc import AsyncIterator
from typing import Any

import anyio
import pytest
from anyio.abc import ByteSendStream
from hypothesis import given, settings
from hypothesis import strategies as st

from jev_judge_mcp.extract.candidates import REGEX_TIMEOUT_REASON, REGEX_TIMEOUT_S
from jev_judge_mcp.extract.dialect import to_units, translate
from jev_judge_mcp.extract.executor import Invalid, Matches, Saturated, Timeout, Unavailable
from jev_judge_mcp.extract.worker import ProcessRegexExecutor
from jev_judge_mcp.limits import EXTRACT
from jev_judge_mcp.settings import Settings
from jev_judge_mcp.tools import TOOLS, Runtime, Toolset
from jev_judge_mcp.validation import validate_choice, validate_extract_choice
from tests.support.jev import FakeProvider, call_tool, text_of

pytestmark = pytest.mark.anyio

DOCUMENT = "Build ABC-123 passed. Build ABC-124 failed."
SLOW = {"id": "slow", "pattern": "(a|aa)+$", "description": "Pathological."}
SLOW_DOCUMENT = "a" * 40 + "!"


@pytest.fixture(scope="module")
def anyio_backend() -> str:
    return "asyncio"


def deadline() -> float:
    return time.monotonic() + REGEX_TIMEOUT_S


def build(pattern: str = "[A-Z]{3}-\\d+") -> dict[str, Any]:
    return {"document": DOCUMENT, "fields": [{"id": "build", "pattern": pattern, "description": "The build id."}]}


async def test_no_candidates_means_no_request_and_no_provider_resolution() -> None:
    def unresolvable(_: Settings) -> FakeProvider:
        raise AssertionError("the provider was resolved")

    toolset = Toolset(Runtime(Settings(), provider_factory=unresolvable), TOOLS)
    try:
        result = await toolset.call("jev_extract", build("XYZ-\\d+"))
    finally:
        await toolset.aclose()
    assert not result.is_error
    text = text_of(result)
    assert '"provider": "none"' in text
    assert '"usage": null' in text
    assert '"reason": "no_regex_matches"' in text


async def test_value_is_the_verbatim_candidate_even_across_surrogates() -> None:
    arguments = {"document": "x😀y", "fields": [{"id": "half", "pattern": "\\ude00.", "description": "d"}]}
    answers = {"f0": {"choice": "c0", "probabilities": {"c0": 0.95, "none_of_them": 0.05}, "confidence": 0.9}}
    outcome = await call_tool("jev_extract", arguments, answers)
    assert outcome.payload["results"][0]["value"] == "\ude00y"
    assert '"value": "\\ude00y"' in outcome.text


def test_extract_sum_tolerance_is_its_own() -> None:
    """Q2 (ADR-0012): a 0.99 sum fails extract's check and passes the shared one."""
    answer = {"choice": "c0", "probabilities": {"c0": 0.94, "none_of_them": 0.05}}
    assert validate_extract_choice(answer, ["c0", "none_of_them"]) is None
    assert validate_choice(answer, ["c0", "none_of_them"]) is not None
    exact = {"choice": "c0", "probabilities": {"c0": 0.95, "none_of_them": 0.05}}
    assert validate_extract_choice(exact, ["c0", "none_of_them"]) is not None


probability: st.SearchStrategy[Any] = st.one_of(
    st.floats(allow_nan=True, allow_infinity=True), st.integers(-2, 2), st.text(max_size=2)
)
answers: st.SearchStrategy[Any] = st.one_of(
    st.none(),
    st.text(max_size=3),
    st.fixed_dictionaries(  # pyright: ignore[reportUnknownArgumentType]
        {"choice": st.one_of(st.sampled_from(["c0", "c1", "c2", "none_of_them", "c9"]), st.integers())},
        optional={
            "probabilities": st.one_of(
                st.dictionaries(st.sampled_from(["c0", "c1", "c2", "none_of_them", "x"]), probability, max_size=5),
                st.lists(probability, max_size=3),
            ),
            "confidence": probability,
        },
    ),
)


PICKED = ["ABC-1", "ABC-22", "ABC-333"]
PICK_ARGUMENTS = {
    "document": " ".join(PICKED),
    "fields": [{"id": "build", "pattern": "[A-Z]{3}-\\d+", "description": "The build id."}],
}


@pytest.fixture(scope="module")
async def picker() -> AsyncIterator[tuple[Toolset, FakeProvider]]:
    """One toolset (and regex pool) for every example; each example swaps the provider's answers."""
    provider = FakeProvider({})
    toolset = Toolset(Runtime(Settings(), provider_factory=lambda _: provider), TOOLS)
    try:
        yield toolset, provider
    finally:
        await toolset.aclose()


@settings(max_examples=300, deadline=None)
@given(answer=answers, thresholds=st.tuples(st.floats(0, 1), st.floats(0, 1)))
async def test_value_is_a_candidate_or_null(
    picker: tuple[Toolset, FakeProvider], answer: Any, thresholds: tuple[float, float]
) -> None:
    toolset, provider = picker
    provider.answers = {"f0": answer}
    arguments = {**PICK_ARGUMENTS, "auto_accept": thresholds[0], "minimum_margin": thresholds[1]}
    result = await toolset.call("jev_extract", arguments)
    assert not result.is_error
    row = json.loads(text_of(result))["results"][0]
    assert row["value"] is None or row["value"] in PICKED


async def test_timeout_is_invalid_pattern_within_budget_while_other_calls_run() -> None:
    executor = ProcessRegexExecutor(size=4)  # the default follows the host's CPU count
    await executor.warm()
    toolset = Toolset(Runtime(Settings(), lambda _: FakeProvider({}), executor), TOOLS)
    finished: dict[str, float] = {}
    slow_text: list[str] = []
    started = time.monotonic()

    async def slow() -> None:
        result = await toolset.call("jev_extract", {"document": SLOW_DOCUMENT, "fields": [SLOW]})
        finished["slow"] = time.monotonic() - started
        slow_text.append(text_of(result))

    async def quick(index: int) -> None:
        await anyio.sleep(0.05)
        await toolset.call("jev_extract", build())
        await toolset.call("jev_verify", {"claims": ["c"], "evidence": "e"})
        finished[f"quick{index}"] = time.monotonic() - started

    try:
        async with anyio.create_task_group() as tg:
            tg.start_soon(slow)
            for index in range(6):
                tg.start_soon(quick, index)
    finally:
        await toolset.aclose()

    assert REGEX_TIMEOUT_REASON in slow_text[0]
    assert '"status": "invalid_pattern"' in slow_text[0]
    assert 1.0 <= finished["slow"] < 1.2
    assert max(t for name, t in finished.items() if name != "slow") < 0.6


async def test_cancelled_call_kills_its_worker_and_the_pool_recovers() -> None:
    pool = ProcessRegexExecutor(size=1)
    await pool.warm()
    pid = pool._idle[0].process.pid  # pyright: ignore[reportPrivateUsage]
    slow = translate("(a|aa)+$", "g")
    with anyio.move_on_after(0.2):
        await pool.find(slow, to_units(SLOW_DOCUMENT), deadline=deadline())
    assert pool._idle == []  # pyright: ignore[reportPrivateUsage]
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)
    started = time.monotonic()
    matches = await pool.find(translate("[A-Z]{3}-\\d+", "g"), to_units(DOCUMENT), deadline=deadline())
    assert matches == Matches(["ABC-123", "ABC-124"], False, 0)
    assert time.monotonic() - started < 2
    await pool.aclose()


async def test_a_spent_deadline_kills_its_worker_and_the_next_demand_is_served() -> None:
    """The deadline path mirrors the cancel path: the slot that ran out is killed, the pool rests
    with no worker until the next demand, and that demand is answered by one fresh worker."""
    pool = ProcessRegexExecutor(size=1)
    await pool.warm()
    pid = pool._idle[0].process.pid  # pyright: ignore[reportPrivateUsage]
    result = await pool.find(translate("(a|aa)+$", "g"), to_units(SLOW_DOCUMENT), deadline=deadline())
    assert result == Timeout()
    assert pool._idle == []  # pyright: ignore[reportPrivateUsage]
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)
    matches = await pool.find(translate("[A-Z]{3}-\\d+", "g"), to_units(DOCUMENT), deadline=deadline())
    assert matches == Matches(["ABC-123", "ABC-124"], False, 0)
    await pool.aclose()


async def test_cancel_during_start_reaps_the_pid(monkeypatch: pytest.MonkeyPatch) -> None:
    """ADR-0011: a cancel before the worker is ready still kills that process.

    `_find` kills only a slot it has taken. Until `_start` returns, the process is `_start`'s.
    """
    started = anyio.Event()
    pids: list[int] = []
    real_open = anyio.open_process

    async def unready_process(*_args: object, **_kwargs: object) -> anyio.abc.Process:
        process = await real_open(
            [sys.executable, "-c", "import time; time.sleep(60)"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=None,
        )
        pids.append(process.pid)
        started.set()
        return process

    monkeypatch.setattr(anyio, "open_process", unready_process)
    pool = ProcessRegexExecutor(size=1)

    async def match() -> None:
        await pool.find(translate("a", "g"), to_units("a"), deadline=time.monotonic() + 30)

    try:
        with anyio.fail_after(5):
            async with anyio.create_task_group() as tg:
                tg.start_soon(match)
                await started.wait()
                tg.cancel_scope.cancel()
        assert pids
        with pytest.raises(ProcessLookupError):
            os.kill(pids[0], 0)
        assert pool._idle == []  # pyright: ignore[reportPrivateUsage]
    finally:
        await pool.aclose()
        for pid in pids:
            with contextlib.suppress(ProcessLookupError):
                os.kill(pid, signal.SIGKILL)


async def test_cancel_delivered_inside_the_spawn_leaves_no_orphan(monkeypatch: pytest.MonkeyPatch) -> None:
    """ADR-0011: the spawn window itself is covered.

    A cancel delivered while `_start` is suspended inside `anyio.open_process` — before any
    kill-on-cancel handler exists — must still leave no orphan worker after `aclose()`.
    """
    spawned = anyio.Event()
    release = anyio.Event()
    pids: list[int] = []
    real_open = anyio.open_process

    async def open_then_wait_for_cancel(*args: object, **kwargs: object) -> anyio.abc.Process:
        process = await real_open(*args, **kwargs)  # pyright: ignore[reportArgumentType]
        pids.append(process.pid)
        spawned.set()
        await release.wait()  # suspended inside `open_process`: a cancel delivered here is the test's
        return process

    monkeypatch.setattr(anyio, "open_process", open_then_wait_for_cancel)
    pool = ProcessRegexExecutor(size=1)

    async def match() -> None:
        await pool.find(translate("a", "g"), to_units("a"), deadline=time.monotonic() + 30)

    try:
        with anyio.fail_after(5):
            async with anyio.create_task_group() as tg:
                tg.start_soon(match)
                await spawned.wait()
                tg.cancel_scope.cancel()
                release.set()
        assert pids
        with pytest.raises(ProcessLookupError):
            os.kill(pids[0], 0)
        assert pool._idle == []  # pyright: ignore[reportPrivateUsage]
    finally:
        await pool.aclose()
        for pid in pids:
            with contextlib.suppress(ProcessLookupError):
                os.kill(pid, signal.SIGKILL)


async def test_spawn_failure_is_the_not_started_refusal(monkeypatch: pytest.MonkeyPatch) -> None:
    """EMFILE at spawn stays inside the executor contract: `Invalid(NOT_STARTED)`, never an escape."""

    async def no_file_descriptors(*_args: object, **_kwargs: object) -> anyio.abc.Process:
        raise OSError(24, "Too many open files")

    monkeypatch.setattr(anyio, "open_process", no_file_descriptors)
    pool = ProcessRegexExecutor(size=1)
    try:
        result = await pool.find(translate("[A-Z]{3}-\\d+", "g"), to_units(DOCUMENT), deadline=time.monotonic() + 5)
        assert result == Invalid(Unavailable.NOT_STARTED)
    finally:
        await pool.aclose()


async def test_a_dead_idle_worker_is_answered_by_one_fresh_worker() -> None:
    """A worker an OOM kill took between jobs costs one respawn, not a `worker_error` refusal."""
    pool = ProcessRegexExecutor(size=1)
    await pool.warm()
    dead = pool._idle[0]  # pyright: ignore[reportPrivateUsage]
    pid = dead.process.pid
    await dead.kill()  # the slot stays pooled with a dead pipe, as between two jobs
    matches = await pool.find(translate("[A-Z]{3}-\\d+", "g"), to_units(DOCUMENT), deadline=time.monotonic() + 10)
    assert matches == Matches(["ABC-123", "ABC-124"], False, 0)
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)
    await pool.aclose()


class _StalledSend(ByteSendStream):
    """A stdin that accepts the job only after the caller's deadline has gone."""

    async def send(self, item: bytes) -> None:
        del item
        await anyio.sleep(30)

    async def aclose(self) -> None:
        return None


async def test_the_deadline_covers_sending_the_job() -> None:
    """ADR-0016: writing the job spends the same budget as the reply. A stalled send times out."""
    pool = ProcessRegexExecutor(size=1)
    await pool.warm()
    slot = pool._idle[0]  # pyright: ignore[reportPrivateUsage]
    pid = slot.process.pid
    slot.requests = _StalledSend()
    started = time.monotonic()
    try:
        with anyio.fail_after(2):
            result = await pool.find(translate("a", "g"), to_units("a"), deadline=started + 0.3)
        assert result == Timeout()
        assert 0.2 <= time.monotonic() - started < 1.5
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)
    finally:
        await pool.aclose()
        with contextlib.suppress(ProcessLookupError):
            os.kill(pid, signal.SIGKILL)


async def test_a_full_pool_times_out_within_one_request_budget() -> None:
    """ADR-0016: the deadline is taken before admission, so queue wait spends the same 1 s budget."""
    pool = ProcessRegexExecutor(size=1)
    slow = translate("(a|aa)+$", "g")
    outcomes: list[object] = []

    async def run() -> None:
        outcomes.append(await pool.find(slow, to_units(SLOW_DOCUMENT), deadline=deadline()))

    started = time.monotonic()
    async with anyio.create_task_group() as tg:
        tg.start_soon(run)
        tg.start_soon(run)
    elapsed = time.monotonic() - started
    await pool.aclose()
    assert outcomes == [Timeout(), Timeout()]
    assert 1.0 <= elapsed < 1.8


async def test_admission_refuses_patterns_beyond_the_queue_bound() -> None:
    """ADR-0016: admission is bounded — a pattern the pool cannot serve fails without running."""
    pool = ProcessRegexExecutor(size=1, queue_bound=0)
    started = time.monotonic()
    refused = await pool.find(translate("[A-Z]{3}-\\d+", "g"), to_units(DOCUMENT), deadline=deadline())
    assert refused == Saturated()
    assert time.monotonic() - started < 0.5
    await pool.aclose()


def test_an_orphaned_worker_ends_itself_after_the_deadline() -> None:
    """A worker whose server died without killing it stops on its own shortly after the deadline."""
    caps = (EXTRACT.candidates_per_field, EXTRACT.candidate_units)
    job = pickle.dumps((translate("(a|aa)+$", "g").source, 0, to_units(SLOW_DOCUMENT), 0.2, *caps))
    started = time.monotonic()
    worker = subprocess.run(
        [sys.executable, "-m", "jev_judge_mcp.extract.worker"],
        input=len(job).to_bytes(4) + job,
        capture_output=True,
        timeout=10,
        check=False,
    )
    assert worker.returncode == -signal.SIGALRM
    assert time.monotonic() - started < 3


async def test_saturated_admission_reports_capacity_not_a_pattern_problem() -> None:
    """ADR-0025: the queue-bound refusal is its own reason, never the timeout text."""
    executor = ProcessRegexExecutor(size=1, queue_bound=0)
    toolset = Toolset(Runtime(Settings(), lambda _: FakeProvider({}), executor), TOOLS)
    try:
        first = await toolset.call("jev_extract", build())
        second = await toolset.call("jev_extract", build())
    finally:
        await toolset.aclose()
    assert '"status": "invalid_pattern"' in text_of(second)
    assert '"reason": "regex_pool_saturated"' in text_of(second)
    assert "timed out" not in text_of(second)
    assert "simplify the pattern" not in text_of(second)
    assert '"reason": "regex timed out' not in text_of(second)
    del first
