"""tools/list against the TS 0.5.0 snapshot under the ADR-0010 diff, and the one-registry invariant (ADR-0013).

`MCPServer`'s decorator path cannot publish the snapshot: it derives `inputSchema` from the
signature (adding `title` keys), adds an `outputSchema`, and never emits `execution`. Hand-authored
`mcp.types.Tool` definitions published through one `Toolset` reproduce all ten snapshot tools
exactly on the wire — and the same registry answers `tools/call`, so listed and callable cannot
drift apart.
"""

import copy
import sys
from typing import Any

import pytest

from jev_judge_mcp.server import build_server
from jev_judge_mcp.settings import load_settings
from tests.support.jev import text_of
from tests.support.stdio import StdioServer
from tests.support.tools_list import field_mismatches, load_snapshot, order_mismatch, sort_keys

pytestmark = pytest.mark.anyio

SNAPSHOT_TOOL_COUNT = 10
EXTENSION_TOOLS = ("jev_score",)
"""Published after the snapshot ten (ADR-0048, divergence `score-tool-extension`): the frozen
surface stays a prefix; the extension carries its own pinning tests (`test_score_tool.py`)."""

# Runs the real server over stdio, publishing the snapshot definitions through a stub Toolset —
# the one registry, with no callable behavior behind it.
_PUBLISH_SNAPSHOT = """
from mcp.types import Tool
import anyio
from jev_judge_mcp.server import JevMCPServer, configure_logging, serve
from jev_judge_mcp.settings import load_settings
from jev_judge_mcp.tools import JevTool, Runtime, Toolset
from tests.support.tools_list import load_snapshot

async def stub(args, runtime):
    raise NotImplementedError

settings = load_settings()
configure_logging(settings.log_level)
tools = [JevTool(definition=Tool.model_validate(tool), handler=stub) for tool in load_snapshot()]
anyio.run(serve, JevMCPServer(toolset=Toolset(Runtime(settings), tools), log_level=settings.log_level), settings)
"""


def served_tools(command: list[str] | None = None) -> list[dict[str, Any]]:
    with StdioServer(command) as server:
        server.initialize()
        reply = server.request({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        server.close_stdin()
        server.wait()
    tools: list[dict[str, Any]] = reply["result"]["tools"]
    return tools


def test_snapshot_shape() -> None:
    snapshot = load_snapshot()
    assert len(snapshot) == SNAPSHOT_TOOL_COUNT
    assert all(tool["execution"] == {"taskSupport": "forbidden"} for tool in snapshot)


def test_sort_keys_is_recursive_and_order_insensitive() -> None:
    a = {"b": [{"y": 1, "x": {"q": 2, "p": 3}}], "a": 0}
    b = {"a": 0, "b": [{"x": {"p": 3, "q": 2}, "y": 1}]}
    assert list(sort_keys(a)) == ["a", "b"]
    assert sort_keys(a) == sort_keys(b)


def test_diff_ignores_key_order_and_uncompared_fields() -> None:
    snapshot = load_snapshot()
    served = [dict(reversed(list(tool.items()))) | {"outputSchema": {"type": "object"}} for tool in snapshot]
    assert field_mismatches(snapshot, served) == []
    assert order_mismatch(snapshot, served) is None


@pytest.mark.parametrize("field", ["name", "title", "description", "inputSchema", "execution"])
def test_diff_flags_each_compared_field(field: str) -> None:
    snapshot = load_snapshot()
    served = copy.deepcopy(snapshot[:1])
    if field == "inputSchema":
        served[0]["inputSchema"]["properties"]["claims"]["minItems"] = 2
    elif field == "execution":
        del served[0]["execution"]
    else:
        served[0][field] = served[0][field] + "x"
    assert field_mismatches(snapshot, served) != []


def test_diff_flags_order() -> None:
    snapshot = load_snapshot()
    assert order_mismatch(snapshot, [snapshot[1], snapshot[0]]) is not None


def test_published_snapshot_round_trips_on_the_wire() -> None:
    snapshot = load_snapshot()
    served = served_tools([sys.executable, "-c", _PUBLISH_SNAPSHOT])
    assert field_mismatches(snapshot, served) == []
    assert order_mismatch(snapshot, served) is None
    assert len(served) == SNAPSHOT_TOOL_COUNT
    assert all("outputSchema" not in tool for tool in served)  # ADR-0006: no output schemas in 1.0


def test_served_tools_match_snapshot() -> None:
    """The snapshot ten stay byte-identical and first; the extensions follow, in their own order."""
    snapshot = load_snapshot()
    served = served_tools()
    assert field_mismatches(snapshot, served[: len(snapshot)]) == []
    assert [tool["name"] for tool in served] == [tool["name"] for tool in snapshot] + list(EXTENSION_TOOLS)
    assert all("outputSchema" not in tool for tool in served)  # ADR-0006


def test_served_tool_set_is_complete() -> None:
    served = served_tools()
    if len(served) < SNAPSHOT_TOOL_COUNT:
        pytest.skip(f"{len(served)}/{SNAPSHOT_TOOL_COUNT} tools served; the full set lands in P5")
    assert [tool["name"] for tool in served] == [tool["name"] for tool in load_snapshot()] + list(EXTENSION_TOOLS)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


async def test_published_names_equal_callable_names() -> None:
    """ADR-0013: one registry — every listed tool is callable, every callable tool is listed."""
    server = build_server(load_settings())
    listed = {tool.name for tool in await server.list_tools()}
    assert listed == set(server.toolset.names())
    assert listed == {tool["name"] for tool in load_snapshot()} | set(EXTENSION_TOOLS)


async def test_unknown_tool_is_a_deterministic_parity_shape() -> None:
    """The unknown-tool result is owned text (ADR-0013), not whatever a dict lookup raises."""
    server = build_server(load_settings())
    result = await server.call_tool("jev_nope", {})
    assert result.is_error
    assert text_of(result) == "MCP error -32602: Tool jev_nope not found"
