"""The replay engine for the parity corpus (ADR-0015).

Builds on `tests.support.fixtures`, the loader: this module knows how a recorded call is
replayed, nothing about where fixtures live. The fixture call's `env` configures the
server with `{{FAKE_URL}}` resolved to the fake provider, the fake speaks the `compatible`
envelope by serving the recorded responses in order, the real toolset makes the call, and
the outcome must equal the recording byte for byte — request bodies in key order, result
text, and `isError` — unless `tests.parity.divergences.DIVERGENT` sanctions a difference.
`tests/parity/test_tool_fixtures.py` is a thin parametrized consumer.
"""

import json
from typing import Any

import httpx
import pytest
import respx

from jev_judge_mcp.settings import load_settings
from jev_judge_mcp.tools import TOOLS, Runtime, Toolset
from tests.parity.divergences import DIVERGENT, Expectation
from tests.support.fixtures import FixtureCall, divergences

FAKE_URL = "http://fake.invalid"


def fake_url(value: str) -> str:
    """Fixture payloads record `{{FAKE_URL}}` where a request must reach the fake provider."""
    return value.replace("{{FAKE_URL}}", FAKE_URL)


def apply_call_env(call: FixtureCall, monkeypatch: pytest.MonkeyPatch) -> None:
    """Install the fixture call's `env`, `{{FAKE_URL}}` resolved to the fake provider."""
    # Pin the key file away from the developer's machine first (ADR-0046): no resolver-error
    # fixture may depend on whether this host ran `setup`. A fixture that names it still wins.
    monkeypatch.setenv("JEV_MCP_KEY_FILE", "/nonexistent/jev-mcp-key")
    for key, value in call.payload["env"].items():
        monkeypatch.setenv(key, fake_url(value))


def ordered(value: Any) -> str:
    """Parsed JSON as text, key order kept: equality checks keys, values, and order together."""
    return json.dumps(value, ensure_ascii=False)


async def replay_call(call: FixtureCall, monkeypatch: pytest.MonkeyPatch) -> None:
    """Replay one fixture call through the real toolset and require byte parity.

    Every request the tool makes must match its recorded exchange — method, path,
    authorization, content type, and body key order — and the result must reproduce the
    recorded text and `isError`. A call whose fixture carries a divergence tag is checked
    against its Python expectation in `DIVERGENT` instead.
    """
    apply_call_env(call, monkeypatch)
    exchanges: list[dict[str, Any]] = call.payload["exchanges"]
    expected_bodies = [exchange["request"]["body"] for exchange in exchanges]
    expected_text: str = call.payload["result"]["content"][0]["text"]
    expected_error = bool(call.payload["result"].get("isError"))
    if divergences(call):
        expectation: Expectation = DIVERGENT[call.id]
        expected_bodies, expected_text, expected_error = expectation(expected_bodies, expected_text, expected_error)

    sent: list[dict[str, Any]] = []

    def respond(request: httpx.Request) -> httpx.Response:
        index = len(sent)
        assert index < len(exchanges), f"{call.id}: more provider requests than the reference made"
        recorded = exchanges[index]["request"]
        assert request.method == recorded["method"], call.id
        assert request.url.path == recorded["path"], call.id
        assert request.headers["authorization"] == recorded["authorization"], call.id
        assert request.headers["content-type"] == recorded["content_type"], call.id
        sent.append(json.loads(request.content))
        response = exchanges[index]["response"]
        return httpx.Response(response["status"], content=response["body"].encode())

    toolset = Toolset(Runtime(load_settings()), TOOLS)
    with respx.mock(assert_all_mocked=True, assert_all_called=False) as router:
        router.route(host="fake.invalid").mock(side_effect=respond)
        try:
            result = await toolset.call(call.payload["tool"], call.payload["arguments"])
        finally:
            await toolset.aclose()

    assert [ordered(body) for body in sent] == [ordered(body) for body in expected_bodies], call.id
    assert len(result.content) == 1, call.id
    content = result.content[0]
    assert content.type == "text", call.id
    assert content.text == expected_text, call.id
    assert bool(result.is_error) == expected_error, call.id
