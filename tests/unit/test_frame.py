"""The success payload frame (ADR-0013 amendment): `tool, model, provider` first, `usage` last."""

from collections.abc import Mapping
from typing import Any

import pytest

from jev_judge_mcp.domain import Usage
from jev_judge_mcp.providers import Evaluation
from jev_judge_mcp.tools.base import frame
from tests.security.tools import CASES, ToolCase
from tests.support.jev import call_tool

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def test_frame_puts_the_evaluation_head_first_and_usage_last() -> None:
    evaluation = Evaluation({}, Usage(3, 1), "typesafe", "jev-1.13.0")
    payload = frame("jev_verify", evaluation, {"results": [], "summary": {}})
    assert list(payload) == ["tool", "model", "provider", "results", "summary", "usage"]
    assert payload["tool"] == "jev_verify"
    assert payload["model"] == "jev-1.13.0"
    assert payload["provider"] == "typesafe"
    assert payload["usage"] == evaluation.usage.to_wire()


def test_frame_returns_request_id_only_when_the_provider_sent_one() -> None:
    from jev_judge_mcp.providers.base import request_id_of

    bare = Evaluation({}, Usage(1, 1), "compatible", "jev-latest")
    assert "request_id" not in frame("jev_verify", bare, {"results": []})
    sent = Evaluation({}, Usage(1, 1), "compatible", "jev-latest", request_id="req-9")
    assert frame("jev_verify", sent, {"results": []})["request_id"] == "req-9"
    assert request_id_of({"answers": {}}) is None
    assert request_id_of({"request_id": "from-body"}) == "from-body"
    assert request_id_of({}, {"X-Request-Id": "from-header"}) == "from-header"
    assert request_id_of({}, {"X-Typesafe-Request-Id": "live-header"}) == "live-header"


def test_frame_without_an_ask_reports_no_provider_and_null_usage() -> None:
    payload = frame("jev_extract", None, {"summary": {}}, model="jev-1.13.0")
    assert payload == {"tool": "jev_extract", "model": "jev-1.13.0", "provider": "none", "summary": {}, "usage": None}
    assert list(payload) == ["tool", "model", "provider", "summary", "usage"]


@pytest.mark.parametrize("answers", ["permissive", "hostile", "empty"])
@pytest.mark.parametrize("case", CASES, ids=[case.tool for case in CASES])
async def test_every_success_payload_is_framed(case: ToolCase, answers: str) -> None:
    empty: Mapping[str, Any] = {}
    given = {"permissive": case.permissive, "hostile": case.hostile, "empty": empty}[answers]
    outcome = await call_tool(case.tool, case.arguments, given)
    if outcome.is_error:
        return  # only success payloads are framed; the gate refusal and tool errors are not
    keys = list(outcome.payload)
    assert keys[:3] == ["tool", "model", "provider"]
    assert keys[-1] == "usage"
    assert outcome.payload["tool"] == case.tool
