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
EXTENSION_TOOLS = ("jev_score", "jev_file_judge", "jev_ask", "jev_files_judge", "jev_gate_range")
"""Published after the snapshot ten, in this order (ADR-0048 divergence `score-tool-extension`,
ADR-0077 divergences `ask-tool-extension` and `file-judge-tools`, ADR-0080): the frozen surface
stays a prefix; each extension carries its own pinning tests (`test_score_tool.py`,
`test_file_judge.py`, `test_ask.py`, `test_files_judge.py`, `test_gate_range.py`)."""

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


_DESCRIPTION_CHANGES = {
    "jev_verify": ("claims",),
    "jev_find": ("candidates",),
    "jev_classify": ("items", "classes"),
    "jev_decide": ("candidates", "requirements"),
    "jev_rerank": ("candidates",),
    "jev_compare": ("aspects",),
    "jev_extract": ("fields",),
    "jev_review": ("diff",),
    "jev_gate": ("claims", "tests", "diff"),
}


def _description(node: dict[str, Any]) -> str:
    text = node["description"]
    assert isinstance(text, str) and text.strip()
    return text


def _expected_tool(frozen: dict[str, Any], actual: dict[str, Any]) -> dict[str, Any]:
    expected = copy.deepcopy(frozen)
    name = frozen["name"]
    props = expected["inputSchema"]["properties"]
    current = actual["inputSchema"]["properties"]
    for field in _DESCRIPTION_CHANGES.get(name, ()):
        props[field]["description"] = _description(current[field])
    if name == "jev_gate":
        expected["description"] = _description(actual)
        props["review_at"]["description"] = (
            "Low-score diagnostic threshold. An uncited concern requires review. Must be <= "
            "auto_accept. Default min(0.5, auto_accept)."
        )
    if name in {"jev_gate", "jev_verify"}:
        variants = props["evidence"]["anyOf"]
        served = current["evidence"]["anyOf"]
        variants[2]["description"] = _description(served[2])
        for want, got in ((variants[1], served[1]), (variants[2]["items"], served[2]["items"])):
            for field in ("kind", "role"):
                want["properties"][field] = {"type": "string", "description": _description(got["properties"][field])}
    if name in {"jev_gate", "jev_review"}:
        legacy = props["diff"]
        description = legacy.pop("description")
        patch = {
            "type": "object",
            "properties": {field: {"type": "string", "minLength": 1} for field in ("path", "patch")},
            "required": ["path", "patch"],
            "additionalProperties": False,
        }
        props["diff"] = {
            "description": description,
            "anyOf": [legacy, {"type": "array", "minItems": 1, "items": patch}],
        }
    return expected


def test_served_tools_match_snapshot() -> None:
    """Compare the actual registry to the frozen prefix plus its documented schema extensions."""
    snapshot = load_snapshot()
    served = served_tools()
    prefix = served[: len(snapshot)]
    expected = [_expected_tool(want, got) for want, got in zip(snapshot, prefix, strict=True)]
    assert field_mismatches(expected, prefix) == []
    assert [tool["name"] for tool in served] == [tool["name"] for tool in snapshot] + list(EXTENSION_TOOLS)
    assert all("outputSchema" not in tool for tool in served)  # ADR-0006


def test_patch_descriptions_ask_for_the_real_diff() -> None:
    """Divergence `patch-packing-descriptions` (ADR-0013 amendment, 2026-10-07): the frozen text told
    callers to send diff excerpts, which the review then scored low. The served text asks for the
    real diff and names the excerpt cost; the reference's invitation is gone."""
    served = {tool["name"]: tool for tool in served_tools()}
    gate, review = served["jev_gate"], served["jev_review"]
    assert "diff excerpts" not in gate["description"]
    for tool in (gate, review):
        assert "git diff" in tool["inputSchema"]["properties"]["diff"]["description"]
    assert "sent twice" in gate["description"]


def test_initialize_names_each_tool_and_the_gate_rule() -> None:
    """ADR-0061: deferred clients load this string, not a tools/list."""
    from jev_judge_mcp.tools import TOOLS

    with StdioServer() as server:
        reply = server.initialize()
        server.close_stdin()
        server.wait()
    instructions = reply["result"]["instructions"]
    inventory = instructions.split(". ", 1)[0].removeprefix("Tools: ").split(", ")
    # Whole names: `jev_gate` is a prefix of `jev_gate_range`, so a substring count double-counts it.
    for tool in TOOLS:
        assert inventory.count(tool.name) == 1
    assert "recommended final judgment" in instructions
    assert "before claiming done" in instructions
    assert "unless tests, type checks, build, lint" in instructions
    assert "Call jev_gate before claiming done" not in instructions
    assert "Honor action" in instructions
    assert "mcp__" not in instructions
    assert "0.8" not in instructions
    assert "0.5" not in instructions


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


@pytest.mark.parametrize("change", ["required", "constraint", "review_threshold"])
def test_production_comparison_rejects_legacy_schema_regressions(change: str, monkeypatch: pytest.MonkeyPatch) -> None:
    from jev_judge_mcp.tools import TOOLS

    served = [tool.definition.model_dump(by_alias=True, exclude_none=True) for tool in TOOLS]
    schemas = {tool["name"]: tool["inputSchema"] for tool in served}
    schema = schemas["jev_verify"]
    if change == "review_threshold":
        schemas["jev_gate"]["properties"]["review_at"]["maximum"] = 0.9
    elif change == "required":
        schema["required"].append("auto_accept")
    else:
        schema["properties"]["claims"]["minItems"] += 1
    monkeypatch.setattr(sys.modules[__name__], "served_tools", lambda: served)
    with pytest.raises(AssertionError):
        test_served_tools_match_snapshot()
