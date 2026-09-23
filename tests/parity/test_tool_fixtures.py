"""Tool output against the recorded reference (ROADMAP P5).

Every fixture call of an implemented tool is replayed through `tests.support.replay`: the
fixture env configures the server, the fake provider serves the recorded responses in order,
and the Python tool must send the recorded request bodies (key order included) and return the
recorded result text byte for byte, with the same `isError`. Fixtures tagged with a divergence
are replayed against the Python expectation in `DIVERGENT` instead. Fixture calls come from
`tests.support.fixtures`, the one loader (ADR-0015).
"""

import os

import pytest

from tests.parity.divergences import DIVERGENT
from tests.support.fixtures import FixtureCall, divergences, iter_calls
from tests.support.replay import replay_call
from tests.support.stdio import server_env
from tests.support.tools_list import load_snapshot

IMPLEMENTED = {tool["name"] for tool in load_snapshot()}
"""The corpus tools: the reference ten. `jev_score` is an extension with no reference recording;
its pinning tests are tests/contract/test_score_tool.py (ADR-0048)."""

pytestmark = pytest.mark.anyio

ALL_CALLS = list(iter_calls())
CALLS = [call for call in ALL_CALLS if call.payload["tool"] in IMPLEMENTED]


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    kept = server_env()
    for key in list(os.environ):
        if key not in kept:
            monkeypatch.delenv(key)


def test_every_fixture_call_is_replayed() -> None:
    assert len(ALL_CALLS) == 322
    assert len(CALLS) == len(ALL_CALLS)
    assert {call.payload["tool"] for call in ALL_CALLS} == IMPLEMENTED


def test_divergent_fixtures_have_python_expectations() -> None:
    """Every tagged call has a Python expectation, and only tagged calls do: no silent divergence."""
    tagged_calls = {call.id for call in ALL_CALLS if divergences(call)}
    implemented = {call.id for call in CALLS if divergences(call)}
    assert implemented <= DIVERGENT.keys() <= tagged_calls


REGEX_TIMEOUT_FIXTURES = (
    "regex-timeout/extract-catastrophic-then-valid-field",
    "regex-timeout/extract-only-field-times-out",
)


def test_regex_timeout_fixtures_stay_untagged() -> None:
    """ADR-0018: stdlib `re` reproduces the reference's timeouts, so these are not divergences."""
    for fixture_id in REGEX_TIMEOUT_FIXTURES:
        calls = [call for call in ALL_CALLS if call.fixture.id == fixture_id]
        assert calls, fixture_id
        assert not any(divergences(call) for call in calls), fixture_id
        assert not any(call.id in DIVERGENT for call in calls), fixture_id


@pytest.mark.parametrize("call", CALLS, ids=[call.test_id for call in CALLS])
async def test_tool_replays_fixture(monkeypatch: pytest.MonkeyPatch, call: FixtureCall) -> None:
    await replay_call(call, monkeypatch)
