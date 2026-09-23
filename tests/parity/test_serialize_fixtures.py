"""Every recorded tool output re-serializes byte for byte (ADR-0006).

Each fixture's `content[0].text` is `JSON.stringify(payload, null, 2)` from the reference. Parsing it
and running `stringify` over the result must reproduce the text exactly: key order, number format,
escapes, and layout. Fixture calls come from `tests.support.fixtures`, the one loader (ADR-0015).
"""

import json

import pytest

from jev_judge_mcp.serialize import stringify
from tests.support.fixtures import FixtureCall, iter_calls

OUTPUTS = [
    call for call in iter_calls() if call.payload["result"].get("content") and not call.payload["result"].get("isError")
]


def test_fixtures_were_found() -> None:
    assert len(OUTPUTS) > 150


@pytest.mark.parametrize("call", OUTPUTS, ids=[call.test_id for call in OUTPUTS])
def test_recorded_output_round_trips(call: FixtureCall) -> None:
    text: str = call.payload["result"]["content"][0]["text"]
    assert stringify(json.loads(text)) == text, call.id
