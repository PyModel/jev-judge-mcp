"""Provider behavior against the recorded reference (ROADMAP P4).

`resolver-error` fixtures and mock cases 011/012 record the text the reference throws for an env
matrix before any HTTP; `resolve_provider` must raise the same text, except where the fixture carries
`divergence:ADR-0007`. Every recorded exchange spoke the `compatible` envelope, so each one is
replayed through the provider its env resolves to: it must send the recorded request body (key
order included) and headers under the resolved model, and a call the reference failed at the
transport must fail with the same text. Fixture calls come from `tests.support.fixtures`, the one
loader (ADR-0015); `question_from_wire` lives there too.
"""

import json
import os
from typing import Any

import httpx
import pytest
import respx

from jev_judge_mcp.providers import NO_RETRIES, ProviderConfigError, ProviderError, resolve_model, resolve_provider
from jev_judge_mcp.providers.compatible import CompatibleProvider
from jev_judge_mcp.providers.resolver import VERCEL_UNSUPPORTED
from jev_judge_mcp.settings import load_settings
from tests.support.fixtures import FixtureCall, iter_calls, question_from_wire, tagged
from tests.support.replay import FAKE_URL, apply_call_env, ordered
from tests.support.stdio import server_env

pytestmark = pytest.mark.anyio

CALLS = list(iter_calls())
RESOLVER_ERRORS = [
    call
    for call in CALLS
    if call.payload["result"].get("isError")
    and call.payload["result"]["content"][0]["text"].startswith(("JEV_PROVIDER=", "No Jev provider credentials"))
]
EXCHANGES = [
    (f"{call.test_id}/{index}", call, exchange)
    for call in CALLS
    for index, exchange in enumerate(call.payload["exchanges"])
]


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    kept = server_env()
    for key in list(os.environ):
        if key not in kept:
            monkeypatch.delenv(key)


def test_fixtures_were_found() -> None:
    assert len(RESOLVER_ERRORS) == 18  # 16 resolver-error fixtures + mock 011 and 012
    assert len(EXCHANGES) > 150
    assert any(exchange["response"]["status"] != 200 for _, _, exchange in EXCHANGES)


@pytest.mark.parametrize("call", RESOLVER_ERRORS, ids=[c.test_id for c in RESOLVER_ERRORS])
def test_resolver_error_text(monkeypatch: pytest.MonkeyPatch, call: FixtureCall) -> None:
    apply_call_env(call, monkeypatch)
    expected: str = call.payload["result"]["content"][0]["text"]
    if tagged(call, "ADR-0007"):
        expected = VERCEL_UNSUPPORTED

    with pytest.raises(ProviderConfigError) as caught:
        resolve_provider(load_settings())

    assert str(caught.value) == expected, call.id


@pytest.mark.parametrize(("exchange_id", "call", "exchange"), EXCHANGES, ids=[e[0] for e in EXCHANGES])
async def test_compatible_replays_exchange(
    monkeypatch: pytest.MonkeyPatch, exchange_id: str, call: FixtureCall, exchange: dict[str, Any]
) -> None:
    apply_call_env(call, monkeypatch)
    settings = load_settings()
    # Single-attempt by injection (ADR-0057): a fixture's 408/429/5xx response stays deterministic
    # and byte-identical to the recording.
    provider = resolve_provider(settings, retry=NO_RETRIES)
    assert isinstance(provider, CompatibleProvider)
    recorded: dict[str, Any] = exchange["request"]
    response: dict[str, Any] = exchange["response"]
    body = recorded["body"]
    assert resolve_model(settings) == body["model"]
    questions = {name: question_from_wire(wire) for name, wire in body["questions"].items()}

    with respx.mock(assert_all_mocked=True) as router:
        route = router.post(f"{FAKE_URL}{recorded['path']}").mock(
            return_value=httpx.Response(response["status"], content=response["body"].encode())
        )
        try:
            evaluation = await provider.evaluate(body["state"], questions, body["model"], 5)
            error = None
        except ProviderError as caught:
            evaluation, error = None, str(caught)
        finally:
            await provider.aclose()

    sent = route.calls.last.request
    assert ordered(json.loads(sent.content)) == ordered(body), exchange_id
    assert sent.headers["authorization"] == recorded["authorization"]
    assert sent.headers["content-type"] == recorded["content_type"]
    text = call.payload["result"]["content"][0]["text"]
    if error is not None:
        assert call.payload["result"].get("isError"), f"{exchange_id}: {error}"
        assert error == text, exchange_id
    else:
        assert evaluation is not None
        parsed = json.loads(response["body"])
        assert evaluation.answers == parsed["answers"], exchange_id
        if isinstance(parsed.get("model"), str):
            assert evaluation.model == parsed["model"]
        else:
            assert evaluation.model == body["model"]
