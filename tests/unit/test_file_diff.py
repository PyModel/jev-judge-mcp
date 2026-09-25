"""A file list is not silently cut, and an unreviewed file is not auto."""

import pytest

from jev_judge_mcp.limits import GATE
from tests.support.jev import call_tool

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


async def test_an_oversized_file_is_unreviewed_and_not_auto() -> None:
    outcome = await call_tool(
        "jev_review",
        {
            "request": "fix the parser",
            "diff": [{"path": "src/parser.py", "patch": "x" * (GATE.doc_units + 1)}],
        },
        {},
    )
    assert not outcome.is_error, outcome.text
    assert outcome.payload["partial"] is True
    assert outcome.payload["unreviewed_files"] == ["src/parser.py"]
    assert outcome.payload["action"] != "auto"
    assert outcome.requests == []
