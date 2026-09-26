"""The one bounded retry loop, driven through real providers over mock transports (ADR-0057).

`tests/contract/test_providers.py` pins single-attempt behavior with `NO_RETRIES`; here the default
policy runs. Sleep, jitter and the clock are injected, so every retry decision reads a controlled
timeline (ADR-0057: offline, deterministic, runner-independent); only the caller-deadline cut and
the cancellation case still use real (short) `anyio` waits, where a real timer or sleep is the
behavior under test and its margin is structural, not a few tens of milliseconds.
"""

import json
from collections.abc import Callable
from typing import Any

import anyio
import httpx
import httpx2
import pytest
import respx

from jev_judge_mcp.domain import NoulCriteria, NoulQuestion, Question
from jev_judge_mcp.errors import REDACTED, Redactor
from jev_judge_mcp.providers import NO_RETRIES, JevProvider, ProviderError, ProviderTimeoutError, RetryPolicy
from jev_judge_mcp.providers import retry as retry_timing
from jev_judge_mcp.providers.compatible import CompatibleProvider
from jev_judge_mcp.providers.typesafe import TypeSafeProvider
from tests.support.retries import ControlledClock
from tests.support.retries import controlled_clock as controlled_clock
from tests.support.retries import fast_retries as fast_retries

pytestmark = pytest.mark.anyio

SECRET = "jev-retry-not-a-real-key"  # noqa: S105 - a long, distinctive fake, never a real credential
URL = "https://retry.example/v1/systemone"
QUESTIONS: dict[str, Question] = {"q": NoulQuestion("Is it?", NoulCriteria("yes", "no"))}
OK = json.dumps({"answers": {"q": {"type": "noul", "noul": 0.9}}}).encode()
ENVELOPE_BAD = b"not json"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def provider(retry: RetryPolicy | None = None) -> CompatibleProvider:
    """A compatible provider under `retry`, defaulting to the bounded policy (ADR-0057)."""
    return CompatibleProvider(Redactor([SECRET]), api_key=SECRET, base_url=URL, retry=retry)


def serving(responses: list[Any], sent: list[httpx.Request]) -> Callable[[httpx.Request], httpx.Response]:
    """Serve `responses` in order; the last one repeats, so exhausted attempts need no bookkeeping."""

    def respond(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        response = responses.pop(0) if len(responses) > 1 else responses[0]
        if isinstance(response, Exception):
            raise response
        return response

    return respond


async def evaluate(provider: JevProvider, timeout: float | None = 5) -> Any:
    try:
        return await provider.evaluate({"state": True}, QUESTIONS, "jev-latest", timeout)
    finally:
        await provider.aclose()


async def failure(provider: JevProvider, timeout: float | None = 5) -> str:
    with pytest.raises(ProviderError) as caught:
        await evaluate(provider, timeout)
    return str(caught.value)


def spying_fail_after(monkeypatch: pytest.MonkeyPatch) -> list[float | None]:
    """Record every `anyio.fail_after` delay the retry loop arms, in order: the whole-call scope
    first, then each attempt's cap. The real deadlines still run (they simply never fire — the
    timeline the loop reads is the injected clock's, not the runner's)."""
    recorded: list[float | None] = []
    real_fail_after = anyio.fail_after

    def spy(delay: float | None) -> Any:
        recorded.append(delay)
        return real_fail_after(delay)

    monkeypatch.setattr(anyio, "fail_after", spy)
    return recorded


def full_length_stall(
    clock: ControlledClock, sent: list[httpx.Request], caps: list[float | None]
) -> Callable[[httpx.Request], Any]:
    """An attempt that runs its full length and times out: it consumes exactly the cap the loop
    armed it with (the recorded one — the worst case the budget must bound), then fails as a
    transport timeout. The injected clock carries that time; no real waiting happens."""

    async def stall(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        cap = caps[-1]  # this attempt runs inside the scope the loop just armed
        assert cap is not None
        clock.advance(cap)
        raise httpx.ReadTimeout("stalled")

    return stall


# --- Success after transient failures ---


@pytest.mark.parametrize("status", [408, 429, 500, 502, 503, 504])
async def test_success_after_a_transient_status(fast_retries: list[float], status: int) -> None:
    sent: list[httpx.Request] = []
    with respx.mock(assert_all_mocked=True, assert_all_called=False) as router:
        router.post(URL).mock(
            side_effect=serving([httpx.Response(status, text="later"), httpx.Response(200, content=OK)], sent)
        )
        evaluation = await evaluate(provider())

    assert evaluation.answers["q"]["noul"] == 0.9
    assert len(sent) == 2
    assert fast_retries == [0.5]


async def test_success_after_429_honors_retry_after_ms_then_a_capped_retry_after(fast_retries: list[float]) -> None:
    sent: list[httpx.Request] = []
    responses = [
        httpx.Response(429, text="slow down", headers={"retry-after-ms": "250"}),
        httpx.Response(429, text="really", headers={"Retry-After": "3600"}),
        httpx.Response(200, content=OK),
    ]
    with respx.mock(assert_all_mocked=True, assert_all_called=False) as router:
        router.post(URL).mock(side_effect=serving(responses, sent))
        # No whole-call deadline: the 5 s capped hint must fit the caller's budget too.
        evaluation = await evaluate(provider(), timeout=None)

    assert evaluation.answers["q"]["noul"] == 0.9
    assert fast_retries == [0.25, 5.0]  # the hint first, then the hour-long hint capped at backoff_max
    assert len(sent) == 3


async def test_success_after_a_connection_refusal(fast_retries: list[float]) -> None:
    sent: list[httpx.Request] = []
    responses: list[Any] = [httpx.ConnectError("refused"), httpx.Response(200, content=OK)]
    with respx.mock(assert_all_mocked=True, assert_all_called=False) as router:
        router.post(URL).mock(side_effect=serving(responses, sent))
        evaluation = await evaluate(provider())

    assert evaluation.answers["q"]["noul"] == 0.9
    assert len(sent) == 2  # the refused attempt reached the transport and failed before any response
    assert fast_retries == [0.5]


async def test_success_after_a_timeout_failure() -> None:
    """A first attempt the transport reports as timed out is abandoned and retried."""
    policy = RetryPolicy(per_attempt_timeout=0.05)
    sent: list[httpx.Request] = []
    responses: list[Any] = [httpx.ReadTimeout("stalled"), httpx.Response(200, content=OK)]
    with respx.mock(assert_all_mocked=True, assert_all_called=False) as router:
        router.post(URL).mock(side_effect=serving(responses, sent))
        evaluation = await evaluate(provider(policy))

    assert evaluation.answers["q"]["noul"] == 0.9
    assert len(sent) == 2


# --- Exhausted retries ---


@pytest.mark.parametrize(
    ("failures", "expected_detail"),
    [
        ([httpx.Response(503, text="first"), httpx.Response(503, text="last")], "503: last"),
        ([httpx.ConnectError("connection refused")], "connection refused"),
    ],
    ids=["status", "connect"],
)
async def test_exhausted_attempts_name_provider_count_and_last_failure(
    fast_retries: list[float], failures: list[Any], expected_detail: str
) -> None:
    """Retries exhausted: the final error names the provider, the attempt count, and the last
    failure — the status and cut body, or the connection error's cause."""
    sent: list[httpx.Request] = []
    with respx.mock(assert_all_mocked=True, assert_all_called=False) as router:
        router.post(URL).mock(side_effect=serving(failures, sent))
        message = await failure(provider())

    assert message == f"Jev-compatible endpoint request failed after 3 attempts: last failure: {expected_detail}"
    assert len(sent) == 3
    assert fast_retries == [0.5, 1.0]


async def test_exhausted_timeouts_name_the_timeout(fast_retries: list[float]) -> None:
    policy = RetryPolicy(per_attempt_timeout=0.02, max_attempts=2)
    sent: list[httpx.Request] = []
    with respx.mock(assert_all_mocked=True, assert_all_called=False) as router:
        router.post(URL).mock(side_effect=serving([httpx.ReadTimeout("stalled")], sent))
        with pytest.raises(ProviderTimeoutError) as caught:
            await evaluate(provider(policy))

    assert str(caught.value) == (
        "Jev-compatible endpoint request failed after 2 attempts: last failure: the attempt timed out"
    )
    assert len(sent) == 2


async def test_exhausted_text_is_redacted_and_the_body_cut_survives(fast_retries: list[float]) -> None:
    """The last failure keeps ADR-0008's redaction and its 200-unit body cut."""
    sent: list[httpx.Request] = []
    body = "x" * 185 + SECRET + "tail"
    with respx.mock(assert_all_mocked=True, assert_all_called=False) as router:
        router.post(URL).mock(side_effect=serving([httpx.Response(503, text=body)], sent))
        message = await failure(provider())

    assert SECRET not in message
    assert REDACTED in message
    assert "tail" in message  # the cut body is quoted as the last failure's detail


# --- What never retries ---


@pytest.mark.parametrize(
    "transport_error",
    [
        httpx.UnsupportedProtocol("the URL scheme ftp is not supported"),
        httpx.LocalProtocolError("a request this malformed never left the process"),
    ],
    ids=["unsupported-protocol", "local-protocol"],
)
async def test_permanent_transport_errors_fail_without_a_retry(
    fast_retries: list[float], transport_error: Exception
) -> None:
    """R2: only transient transport failures retry; a request that cannot be made or sent is final."""
    sent: list[httpx.Request] = []
    with respx.mock(assert_all_mocked=True, assert_all_called=False) as router:
        router.post(URL).mock(side_effect=serving([transport_error], sent))
        message = await failure(provider())

    assert message == f"Jev-compatible endpoint request failed: {transport_error}"
    assert len(sent) == 1
    assert fast_retries == []


@pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
async def test_other_4xx_fails_without_a_retry(fast_retries: list[float], status: int) -> None:
    sent: list[httpx.Request] = []
    with respx.mock(assert_all_mocked=True, assert_all_called=False) as router:
        router.post(URL).mock(side_effect=serving([httpx.Response(status, text="no")], sent))
        message = await failure(provider())

    assert message == f"Jev-compatible endpoint {status}: no"
    assert len(sent) == 1
    assert fast_retries == []


async def test_envelope_errors_fail_without_a_retry(fast_retries: list[float]) -> None:
    sent: list[httpx.Request] = []
    with respx.mock(assert_all_mocked=True, assert_all_called=False) as router:
        router.post(URL).mock(side_effect=serving([httpx.Response(200, content=ENVELOPE_BAD)], sent))
        message = await failure(provider())

    assert message == "Jev-compatible endpoint returned an invalid response: expected a JSON object."
    assert len(sent) == 1


async def test_a_cross_origin_redirect_is_refused_and_not_retried(fast_retries: list[float]) -> None:
    """ADR-0023's refusal is a permanent failure: one request, today's exact text."""
    sent: list[httpx.Request] = []
    with respx.mock(assert_all_mocked=True, assert_all_called=False) as router:
        router.post(URL).mock(
            side_effect=serving([httpx.Response(307, headers={"Location": "https://evil.example/v1"})], sent)
        )
        away = router.post("https://evil.example/v1").mock(return_value=httpx.Response(200, content=OK))
        message = await failure(provider())

    assert "a redirect left the configured origin and was blocked" in message
    assert len(sent) == 1
    assert not away.called
    assert fast_retries == []


# --- Budgets, cancellation, the off switch ---


async def test_the_overall_budget_stops_retries(monkeypatch: pytest.MonkeyPatch, fast_retries: list[float]) -> None:
    """A retry whose delay would reach the budget is skipped; the final error names both attempts."""
    # Reads: start; attempt 1's remaining budget; attempt 1's elapsed; attempt 2's; attempt 2's elapsed.
    ticks = iter([0.0, 0.0, 0.0, 0.6, 0.6])
    monkeypatch.setattr(retry_timing, "clock", lambda: next(ticks))
    sent: list[httpx.Request] = []
    responses = [httpx.Response(503, text="down"), httpx.Response(503, text="down")]
    with respx.mock(assert_all_mocked=True, assert_all_called=False) as router:
        router.post(URL).mock(side_effect=serving(responses, sent))
        message = await failure(provider(RetryPolicy(budget=1.0)))

    assert message == "Jev-compatible endpoint request failed after 2 attempts: last failure: 503: down"
    assert len(sent) == 2
    assert fast_retries == [0.5]  # the second delay (1.0 from elapsed 0.6) would reach the 1.0 s budget


async def test_a_first_failure_stopped_by_the_budget_keeps_todays_text(
    monkeypatch: pytest.MonkeyPatch, fast_retries: list[float]
) -> None:
    """No retry happened, so the error text is byte-identical to the single-attempt contract."""
    ticks = iter([0.0, 0.0, 9.0])  # start; attempt 1's remaining budget; attempt 1's elapsed
    monkeypatch.setattr(retry_timing, "clock", lambda: next(ticks))
    sent: list[httpx.Request] = []
    with respx.mock(assert_all_mocked=True, assert_all_called=False) as router:
        router.post(URL).mock(side_effect=serving([httpx.Response(503, text="down")], sent))
        message = await failure(provider(RetryPolicy(budget=1.0)))

    assert message == "Jev-compatible endpoint 503: down"
    assert len(sent) == 1
    assert fast_retries == []


async def test_the_overall_budget_bounds_even_full_length_attempts(
    monkeypatch: pytest.MonkeyPatch, controlled_clock: ControlledClock
) -> None:
    """R3: with no caller deadline, every attempt is capped at the budget time left, so the whole
    call never runs past `budget` — the last attempt is shorter than its per-attempt timeout.

    Deterministic timeline: three full-length attempts (each consumes exactly the cap the loop
    armed it with — the worst case the budget must bound), 10 ms backoffs, a 500 ms budget. The
    injected clock carries the timeline, so no assertion depends on runner speed (ADR-0057).
    """
    policy = RetryPolicy(
        per_attempt_timeout=0.2, budget=0.5, backoff_initial=0.01, backoff_max=0.01, backoff_jitter=0.0
    )
    sent: list[httpx.Request] = []
    caps = spying_fail_after(monkeypatch)

    with respx.mock(assert_all_mocked=True, assert_all_called=False) as router:
        router.post(URL).mock(side_effect=full_length_stall(controlled_clock, sent, caps))
        with pytest.raises(ProviderTimeoutError) as caught:
            await evaluate(provider(policy), timeout=None)

    assert str(caught.value) == (
        "Jev-compatible endpoint request failed after 3 attempts: last failure: the attempt timed out"
    )
    assert len(sent) == 3
    assert caps[0] is None  # no caller deadline: the whole-call scope is unbounded
    attempt_caps: list[float] = [cap for cap in caps[1:] if cap is not None]
    assert len(attempt_caps) == 3
    assert attempt_caps[0] == 0.2 and attempt_caps[1] == 0.2
    assert attempt_caps[2] < 0.2  # the budget, not the per-attempt timeout, cut the last attempt
    assert sum(attempt_caps) <= 0.5  # the whole call stays inside the budget
    assert controlled_clock.now == pytest.approx(0.5)  # the budget is consumed exactly, never exceeded
    assert controlled_clock.delays == [0.01, 0.01]


async def test_a_budget_bound_timeout_is_not_reported_as_the_callers(
    monkeypatch: pytest.MonkeyPatch, controlled_clock: ControlledClock
) -> None:
    """R5: a caller deadline larger than the policy budget does not turn the budget-bound timeout of
    the last attempt into the caller's. The budget ends the call; the exhausted-retry text reports it.

    Same deterministic timeline as R3's test — three full-length attempts, 10 ms backoffs, a 500 ms
    budget — plus a caller deadline of 1 s, twice the budget: every attempt cap comes from the
    budget, and the caller's deadline never binds, whatever the runner's speed.
    """
    policy = RetryPolicy(
        per_attempt_timeout=0.2, budget=0.5, backoff_initial=0.01, backoff_max=0.01, backoff_jitter=0.0
    )
    sent: list[httpx.Request] = []
    caps = spying_fail_after(monkeypatch)

    with respx.mock(assert_all_mocked=True, assert_all_called=False) as router:
        router.post(URL).mock(side_effect=full_length_stall(controlled_clock, sent, caps))
        with pytest.raises(ProviderTimeoutError) as caught:
            await evaluate(provider(policy), timeout=1.0)

    assert caps[0] == 1.0  # the caller's deadline armed the whole-call scope …
    attempt_caps: list[float] = [cap for cap in caps[1:] if cap is not None]
    assert attempt_caps == [0.2, 0.2, pytest.approx(0.08)]  # … but the budget capped every attempt
    assert controlled_clock.now == pytest.approx(0.5)  # the budget, not the caller's 1 s, ended the call
    assert str(caught.value) == (
        "Jev-compatible endpoint request failed after 3 attempts: last failure: the attempt timed out"
    )
    assert len(sent) == 3


async def test_cancellation_during_the_backoff_sleep_returns_promptly() -> None:
    """Real sleeps here: the caller's cancellation must cut the wait, not a fake one."""
    sent: list[httpx.Request] = []
    cut = provider()
    cancelled: list[BaseException] = []
    with respx.mock(assert_all_mocked=True, assert_all_called=False) as router:
        router.post(URL).mock(side_effect=serving([httpx.Response(503, text="down")], sent))
        started = anyio.Event()

        async def call() -> None:
            started.set()
            try:
                await cut.evaluate({"state": True}, QUESTIONS, "jev-latest", None)
            except BaseException as error:
                cancelled.append(error)
                raise

        with anyio.move_on_after(1.0):
            async with anyio.create_task_group() as tg:
                tg.start_soon(call)
                await started.wait()
                while not sent:  # attempt 1 ran; the real 0.5 s backoff sleep is where we cut it
                    await anyio.sleep(0.01)
                tg.cancel_scope.cancel()
        await cut.aclose()

    assert len(cancelled) == 1 and isinstance(cancelled[0], anyio.get_cancelled_exc_class())
    assert len(sent) == 1  # the retry never started


async def test_the_typesafe_provider_never_retries_nested(fast_retries: list[float]) -> None:
    """ADR-0057: the SDK is built with `max_retries=0`, so jev's three attempts stay three requests."""
    requested: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requested.append(request)
        return httpx2.Response(200 if len(requested) == 3 else 503, content=OK)

    sdk_provider = TypeSafeProvider(
        Redactor([SECRET]),
        api_key=SECRET,
        base_url="https://typesafe-retry.example",
        transport=httpx2.MockTransport(handler),
    )
    try:
        evaluation = await sdk_provider.evaluate({"state": True}, QUESTIONS, "m", 5)
    finally:
        await sdk_provider.aclose()

    assert evaluation.provider == "typesafe"
    assert len(requested) == 3
    assert fast_retries == [0.5, 1.0]  # jev's policy drove the waits, the SDK's drove none


def typesafe_provider(handler: Callable[[httpx2.Request], httpx2.Response], retry: RetryPolicy | None = None):
    return TypeSafeProvider(
        Redactor([SECRET]),
        api_key=SECRET,
        base_url="https://typesafe-retry.example",
        transport=httpx2.MockTransport(handler),
        retry=retry,
    )


async def test_a_typesafe_reset_with_an_empty_cause_is_retried(fast_retries: list[float]) -> None:
    """R1: an SDK connection error whose cause has no message must be the retryable connection
    failure it is, not a permanent error; its text stays exactly what a single attempt prints."""
    requested: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requested.append(request)
        if len(requested) == 1:
            raise httpx2.ReadError("")
        return httpx2.Response(200, content=OK)

    sdk_provider = typesafe_provider(handler)
    try:
        evaluation = await sdk_provider.evaluate({"state": True}, QUESTIONS, "m", 5)
    finally:
        await sdk_provider.aclose()

    assert evaluation.provider == "typesafe"
    assert len(requested) == 2
    assert fast_retries == [0.5]


@pytest.mark.parametrize(
    ("retry", "expected_text", "expected_requests", "expected_delays"),
    [
        (NO_RETRIES, "TypeSafe API request failed: Connection error: ReadError", 1, []),
        (
            None,
            "TypeSafe API request failed after 3 attempts: last failure: Connection error: ReadError",
            3,
            [0.5, 1.0],
        ),
    ],
    ids=["no-retries-todays-text", "exhausted-names-the-connection-error"],
)
async def test_a_typesafe_reset_is_classified_by_policy(
    fast_retries: list[float],
    retry: RetryPolicy | None,
    expected_text: str,
    expected_requests: int,
    expected_delays: list[float],
) -> None:
    """An SDK reset whose cause has no message keeps today's single-attempt text under the off
    injection, and becomes the exhausted-retries error under the default policy."""
    requested: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requested.append(request)
        raise httpx2.ReadError("")

    sdk_provider = typesafe_provider(handler, retry)
    with pytest.raises(ProviderError) as caught:
        try:
            await sdk_provider.evaluate({"state": True}, QUESTIONS, "m", 5)
        finally:
            await sdk_provider.aclose()

    assert str(caught.value) == expected_text
    assert len(requested) == expected_requests
    assert fast_retries == expected_delays


async def test_a_typesafe_429_honors_retry_after_ms_then_succeeds(fast_retries: list[float]) -> None:
    requested: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requested.append(request)
        if len(requested) == 1:
            return httpx2.Response(429, content=b"slow down", headers={"retry-after-ms": "250"})
        return httpx2.Response(200, content=OK)

    sdk_provider = typesafe_provider(handler)
    try:
        evaluation = await sdk_provider.evaluate({"state": True}, QUESTIONS, "m", None)
    finally:
        await sdk_provider.aclose()

    assert evaluation.provider == "typesafe"
    assert len(requested) == 2
    assert fast_retries == [0.25]


@pytest.mark.parametrize("status", [503, 429])
async def test_the_retry_off_injection_makes_exactly_one_request(fast_retries: list[float], status: int) -> None:
    sent: list[httpx.Request] = []
    with respx.mock(assert_all_mocked=True, assert_all_called=False) as router:
        router.post(URL).mock(side_effect=serving([httpx.Response(status, text="no")], sent))
        message = await failure(provider(NO_RETRIES))

    assert message == f"Jev-compatible endpoint {status}: no"
    assert len(sent) == 1
    assert fast_retries == []


async def test_a_caller_deadline_on_a_single_attempt_keeps_todays_timeout_text() -> None:
    """The whole-call deadline (hook, setup) still reports its own timeout, byte-identical."""
    sent: list[httpx.Request] = []

    async def stall(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        await anyio.sleep(30)
        return httpx.Response(200, content=OK)  # pragma: no cover - cancelled first

    with respx.mock(assert_all_mocked=True, assert_all_called=False) as router:
        router.post(URL).mock(side_effect=stall)
        with pytest.raises(ProviderTimeoutError) as caught:
            await evaluate(provider(), timeout=0.05)

    assert str(caught.value) == "Jev-compatible endpoint request timed out after 0.05 s."
    assert len(sent) == 1
