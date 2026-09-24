"""The retry policy math and its classification (ADR-0057): pure functions, no HTTP, no waiting."""

from typing import ClassVar

import pytest

from jev_judge_mcp.domain import JsonValue, Usage
from jev_judge_mcp.errors import Redactor
from jev_judge_mcp.providers import Evaluation, ProviderError, RetryPolicy
from jev_judge_mcp.providers import retry as retry_timing
from jev_judge_mcp.providers.base import JevProvider, ProviderConfigError, ProviderName
from tests.support.retries import fast_retries as fast_retries


def test_defaults_match_adr_0057() -> None:
    policy = RetryPolicy()
    assert (policy.max_attempts, policy.per_attempt_timeout, policy.backoff_initial) == (3, 30.0, 0.5)
    assert (policy.backoff_max, policy.backoff_jitter, policy.budget) == (5.0, 0.25, 90.0)
    assert policy.statuses == frozenset({408, 429, *range(500, 600)})


def _invalid(**overrides: object) -> RetryPolicy:
    """Build a policy bypassing the constructor's validation, to test that validation directly."""
    policy = RetryPolicy.__new__(RetryPolicy)
    for name, value in {**vars(RetryPolicy()), **overrides}.items():
        object.__setattr__(policy, name, value)
    return policy


@pytest.mark.parametrize(
    "policy",
    [
        _invalid(max_attempts=0),
        _invalid(max_attempts=-1),
        _invalid(per_attempt_timeout=0),
        _invalid(per_attempt_timeout=float("inf")),
        _invalid(backoff_initial=-0.5),
        _invalid(backoff_initial=0),  # zero cannot "disable" backoff: the policy is bounded or it is not
        _invalid(backoff_max=float("nan")),
        _invalid(backoff_max=0),
        _invalid(budget=0),
        _invalid(backoff_jitter=1.5),
        _invalid(backoff_jitter=-0.1),
    ],
)
def test_invalid_policies_are_refused(policy: RetryPolicy) -> None:
    with pytest.raises(ValueError):
        policy.__post_init__()


def test_backoff_doubles_and_is_jittered_subtractively(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(retry_timing, "uniform", lambda: 0.0)
    policy = RetryPolicy()
    assert [retry_timing.backoff_delay(policy, attempt) for attempt in (1, 2, 3, 4)] == [0.5, 1.0, 2.0, 4.0]
    # attempt 5 would be 8.0: capped at backoff_max, never exceeded even with no jitter.
    assert retry_timing.backoff_delay(policy, 5) == 5.0
    monkeypatch.setattr(retry_timing, "uniform", lambda: 0.5)
    assert retry_timing.backoff_delay(policy, 1) == 0.438  # round(0.5 * (1 - 0.5 * 0.25), 3)


def test_retry_after_hint_replaces_backoff_and_is_capped() -> None:
    policy = RetryPolicy()
    assert retry_timing.retry_delay(policy, 1, 0.0, policy.budget, retry_after=0.25) == 0.25
    assert retry_timing.retry_delay(policy, 1, 0.0, policy.budget, retry_after=3600.0) == 5.0


def test_retry_stops_at_max_attempts_and_at_the_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(retry_timing, "uniform", lambda: 0.0)  # full backoff: deterministic budgets
    policy = RetryPolicy()
    assert retry_timing.retry_delay(policy, 3, 0.0, policy.budget) is None  # attempt cap reached
    assert retry_timing.retry_delay(policy, 1, 89.5, policy.budget) is None  # 89.5 + 0.5 reaches budget
    assert retry_timing.retry_delay(policy, 1, 89.4, policy.budget) == 0.5
    # A caller's whole-call budget binds harder than the policy's.
    assert retry_timing.retry_delay(policy, 1, 9.5, 10.0) is None


def test_retry_after_seconds_prefers_ms_and_parses_seconds_and_dates() -> None:
    assert retry_timing.retry_after_seconds({"retry-after-ms": "250"}) == 0.25
    assert retry_timing.retry_after_seconds({"retry-after-ms": "250", "retry-after": "99"}) == 0.25
    assert retry_timing.retry_after_seconds({"retry-after": "120"}) == 120.0
    assert retry_timing.retry_after_seconds({"Retry-After": "0.5"}) == 0.5
    future = retry_timing.retry_after_seconds({"retry-after": "Wed, 21 Oct 2099 07:28:00 GMT"})
    assert future is not None and future > 0


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"retry-after": ""},
        {"retry-after": "soon"},
        {"retry-after": "-5"},
        {"retry-after": "inf"},
        {"retry-after": "Tue, 31 Feb 2020 00:00:00 GMT"},
        {"retry-after-ms": "-1"},
    ],
)
def test_unusable_retry_after_is_none(headers: dict[str, str]) -> None:
    assert retry_timing.retry_after_seconds(headers) is None


# --- Classification (`JevProvider._transient`) ---


class _Classifier(JevProvider):
    """Exposes `_transient` and `_exhausted` without any transport."""

    name: ClassVar[ProviderName] = "compatible"
    label: ClassVar[str] = "Jev-compatible endpoint"

    async def _send(
        self, state: JsonValue, questions: dict[str, JsonValue], model: str, timeout: float | None
    ) -> Evaluation:
        return Evaluation({}, Usage(), "compatible", model)

    async def aclose(self) -> None:
        return None


def _failure(status: int | None = None) -> ProviderError:
    error = ProviderError("boom")
    error.status = status
    return error


def test_status_errors_retry_only_on_the_policy_statuses() -> None:
    provider = _Classifier(Redactor(()))
    transient = provider._transient(_failure(503))  # pyright: ignore[reportPrivateUsage]
    assert transient is not None and transient.kind == "status" and transient.status == 503
    assert transient.describe == "status 503"
    for status in (400, 401, 403, 404, 409, 422):
        assert provider._transient(_failure(status)) is None  # pyright: ignore[reportPrivateUsage]
    # No status: an envelope error, a refusal — never retried.
    assert provider._transient(_failure(None)) is None  # pyright: ignore[reportPrivateUsage]


def test_timeouts_and_connection_failures_are_transient_and_the_rest_is_not() -> None:
    provider = _Classifier(Redactor(()))
    timeout = provider._transient(TimeoutError())  # pyright: ignore[reportPrivateUsage]
    assert timeout is not None and timeout.describe == "timeout"
    connect = provider._transient(ConnectionError("refused"))  # pyright: ignore[reportPrivateUsage]
    assert connect is not None and connect.describe == "connect" and connect.detail == "refused"
    bare = provider._transient(ConnectionError())  # pyright: ignore[reportPrivateUsage]
    assert bare is not None and bare.detail == "ConnectionError"
    assert provider._transient(ValueError("nope")) is None  # pyright: ignore[reportPrivateUsage]


def test_provider_config_error_is_never_transient() -> None:
    error = ProviderConfigError("no key")
    error.status = 503  # even a carried status must not make configuration retryable
    provider = _Classifier(Redactor(()))
    assert provider._transient(error) is None  # pyright: ignore[reportPrivateUsage]
