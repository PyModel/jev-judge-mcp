"""ROADMAP P9 load: local overhead at 1, 4, 16, 32, and 64 concurrent calls (`make load`).

Every tool runs through the real server over the SDK's in-memory transport, so the clock covers
JSON-RPC framing, dispatch, argument validation, regex workers, validation, policy, serialization,
and telemetry at its default setting. The provider is a stub that sleeps `PROVIDER_S` and answers
from the P6 permissive cases; nothing reaches the network. A call's local overhead is its wall time
minus the nominal provider time, so event-loop queueing, late wake-ups, and GC pauses count against
the budget. The fixed provider delay keeps the in-flight calls in lockstep, the worst case for queueing.
A CI canary (`make load_canary`) runs the same path at concurrency 1 and 4, so a question the stub cannot
answer fails `make ci` instead of shipping unseen behind the timing-based `make load`.
"""

import json
import math
from time import perf_counter
from typing import Any, ClassVar, override

import anyio
import pytest
from mcp import Client

from jev_judge_mcp.domain import JsonValue, Usage
from jev_judge_mcp.errors import Redactor
from jev_judge_mcp.providers import Evaluation, JevProvider, ProviderName
from jev_judge_mcp.server import JevMCPServer, freeze_startup_heap
from jev_judge_mcp.settings import Settings
from jev_judge_mcp.tools import TOOLS, Runtime, Toolset
from tests.security.tools import CASES
from tests.support.jev import text_of

pytestmark = [pytest.mark.anyio]

LEVELS = (1, 4, 16, 32, 64)
MIN_CALLS = 320
MIN_BURSTS = 20
"""At least 20 provider round trips per slot, so one gen-2 GC pause (~10-15 ms, about one burst in
eight at 64) lands in the tail instead of deciding the p95 alone."""
PROVIDER_S = 0.02
P50_BUDGET_MS = 5.0
P95_BUDGET_MS = 20.0


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def answer_book() -> dict[str, Any]:
    """Every permissive answer by question key; tools that share a key share the answer."""
    book: dict[str, Any] = {}
    for case in CASES:
        for key, answer in case.permissive.items():
            assert book.setdefault(key, answer) == answer, key
    return book


def answer_for(book: dict[str, Any], key: str, question: JsonValue) -> Any:
    """The case's permissive answer, or a Choice derived from the question's own labels.

    jev_gate asks a `source_<index>` Choice whose labels are the evidence ids of the case it is scoring,
    and no case records that answer. Those labels depend on the arguments, so they come from the question
    itself, not a hardcoded id. Every Score and Noul key is already in the book; a question this cannot
    answer raises, which the canary sees.
    """
    if key in book:
        return book[key]
    if isinstance(question, dict) and question.get("type") == "choice":
        criteria = question.get("criteria")
        if isinstance(criteria, dict) and criteria:
            chosen = next(iter(criteria))
            return {
                "choice": chosen,
                "probabilities": {label: 1.0 if label == chosen else 0.0 for label in criteria},
                "confidence": 0.99,
            }
    raise AssertionError(f"stub cannot answer {key}: unrecognized question {question!r}")


class StubProvider(JevProvider):
    name: ClassVar[ProviderName] = "compatible"
    label: ClassVar[str] = "Stub"

    def __init__(self) -> None:
        super().__init__(Redactor(()))
        self.book = answer_book()

    @override
    async def _send(
        self, state: JsonValue, questions: dict[str, JsonValue], model: str, timeout: float | None
    ) -> Evaluation:
        await anyio.sleep(PROVIDER_S)
        answers = {key: answer_for(self.book, key, question) for key, question in questions.items()}
        return Evaluation(answers, Usage(1, 1), self.name, model)

    @override
    async def aclose(self) -> None:
        pass


def calls_at(concurrency: int) -> int:
    return max(MIN_CALLS, MIN_BURSTS * concurrency)


def percentile(samples: list[float], fraction: float) -> float:
    """Nearest rank."""
    ordered = sorted(samples)
    return ordered[max(0, math.ceil(fraction * len(ordered)) - 1)]


async def overheads(
    client: Client, concurrency: int, calls: int, gate_payloads: list[Any] | None = None
) -> list[float]:
    """Run `calls` calls with `concurrency` in flight, cycling through every tool; overhead in ms each.

    `gate_payloads`, when given, collects each jev_gate payload the loop already made, so a caller can
    read one without a second call.
    """
    samples: list[float] = []
    issued = 0

    async def worker() -> None:
        nonlocal issued
        while issued < calls:
            case = CASES[issued % len(CASES)]
            issued += 1
            start = perf_counter()
            result = await client.call_tool(case.tool, dict(case.arguments))
            elapsed = perf_counter() - start
            assert not result.is_error, (case.tool, result.content)
            if gate_payloads is not None and case.tool == "jev_gate":
                gate_payloads.append(json.loads(text_of(result)))
            samples.append((elapsed - PROVIDER_S) * 1000)

    async with anyio.create_task_group() as tg:
        for _ in range(concurrency):
            tg.start_soon(worker)
    return samples


@pytest.mark.load
async def test_local_overhead_stays_within_budget_up_to_64_concurrent_calls() -> None:
    toolset = Toolset(Runtime(Settings(), provider_factory=lambda _: StubProvider()), TOOLS)
    server = JevMCPServer(toolset=toolset, log_level="WARNING")
    freeze_startup_heap()  # as `main` does
    report: list[str] = []
    failures: list[str] = []
    try:
        async with Client(server) as client:
            await overheads(client, max(LEVELS), max(LEVELS))  # warm-up: regex workers, imports, caches
            for level in LEVELS:
                samples = await overheads(client, level, calls_at(level))
                p50, p95 = percentile(samples, 0.5), percentile(samples, 0.95)
                worst = max(samples)
                report.append(f"{level:>3} concurrent: p50 {p50:6.2f} ms  p95 {p95:6.2f} ms  max {worst:6.2f} ms")
                if p50 >= P50_BUDGET_MS or p95 >= P95_BUDGET_MS:
                    failures.append(f"{level} concurrent: p50 {p50:.2f} ms, p95 {p95:.2f} ms")
    finally:
        await toolset.aclose()
    print("\nlocal overhead per call (provider excluded)\n" + "\n".join(report))
    metrics = toolset.runtime.telemetry.metrics.snapshot()
    calls = sum(value for key, value in metrics.items() if key.startswith("calls{"))
    assert calls == max(LEVELS) + sum(calls_at(level) for level in LEVELS)
    assert "regex_timeouts" not in metrics
    assert failures == []


async def test_every_tool_call_succeeds_at_low_concurrency() -> None:
    """Every tool's real server path finishes at concurrency 1 and 4, the CI-visible load guard.

    `make load` measures overhead and stays out of CI, which is how an unanswered `source_<index>`
    question shipped in 0.5.0. One pass per tool per level fails on the first question the stub
    cannot answer. The derived source answer must name the caller's evidence id: a claim row's
    `invalid_response` does not see a source answer that failed validation.
    """
    toolset = Toolset(Runtime(Settings(), provider_factory=lambda _: StubProvider()), TOOLS)
    server = JevMCPServer(toolset=toolset, log_level="WARNING")
    try:
        async with Client(server) as client:
            gate_payloads: list[Any] = []
            for level in (1, 4):
                samples = await overheads(client, level, len(CASES), gate_payloads)
                assert len(samples) == len(CASES)
            assert gate_payloads, "the canary must exercise jev_gate"
            payload = gate_payloads[-1]
            caller_id = next(case for case in CASES if case.tool == "jev_gate").arguments["evidence"][0]["id"]
            assert payload["verification"]["summary"]["invalid_response"] == 0
            row = payload["verification"]["results"][0]
            assert row["supporting_evidence"] == caller_id, row
            metrics = toolset.runtime.telemetry.metrics.snapshot()
            assert [key for key in metrics if key.startswith("fail_closed")] == []
    finally:
        await toolset.aclose()
