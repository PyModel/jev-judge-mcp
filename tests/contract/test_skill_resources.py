"""A connecting client can fetch the packaged skills (ADR-0071).

The owner is the stdio wire. The wheel path is the uvx smoke test.
"""

import re
from pathlib import Path
from typing import Any, cast

from jev_judge_mcp.instructions import ON_DEMAND, server_instructions
from jev_judge_mcp.skills import packaged_rels, uri_for
from jev_judge_mcp.tools import TOOLS
from tests.support.stdio import StdioServer

ROOT = Path(__file__).resolve().parents[2]
PACKAGED = ROOT / "src" / "jev_judge_mcp" / "skills"
JEV = "jev-skill://jev/SKILL.md"
JEV_MCP = "jev-skill://jev-mcp/SKILL.md"
_CAPABILITIES = frozenset({"experimental", "prompts", "resources", "tools"})


def _disk_rels() -> set[str]:
    found: set[str] = set()
    for path in PACKAGED.rglob("*"):
        if not path.is_file() or path.name == "__init__.py" or "__pycache__" in path.parts:
            continue
        found.add(path.relative_to(PACKAGED).as_posix())
    return found


def _result(reply: dict[str, Any]) -> dict[str, Any]:
    result = reply["result"]
    assert isinstance(result, dict)
    return cast(dict[str, Any], result)


def _records(value: object) -> list[dict[str, Any]]:
    assert isinstance(value, list)
    items = cast(list[object], value)
    records: list[dict[str, Any]] = []
    for item in items:
        assert isinstance(item, dict)
        records.append(cast(dict[str, Any], item))
    return records


def _text(reply: dict[str, Any], uri: str) -> str:
    contents = _records(_result(reply)["contents"])
    assert contents
    assert contents[0]["uri"] == uri
    text = contents[0]["text"]
    assert isinstance(text, str)
    return text


def _prompt_text(reply: dict[str, Any]) -> str:
    messages = _records(_result(reply)["messages"])
    assert messages
    content = messages[0]["content"]
    assert isinstance(content, dict)
    text = cast(dict[str, Any], content)["text"]
    assert isinstance(text, str)
    return text


def test_the_package_ships_only_the_allowlist() -> None:
    assert _disk_rels() == set(packaged_rels())
    assert "jev/PROVENANCE.md" not in _disk_rels()
    assert "jev/agents/openai.yaml" not in _disk_rels()


def test_every_jev_skill_uri_named_in_served_text_resolves() -> None:
    """Every jev-skill:// URI a connecting agent can read must be a served resource.

    The regression: 56ccb0f removed jev/PROVENANCE.md from the allowlist while the routing
    skill's sentence naming `jev-skill://jev/PROVENANCE.md` stayed, so every client was sent
    to a URI resources/list does not serve. The allowlist test above cannot see text.
    """
    served = {uri_for(rel) for rel in packaged_rels()}
    texts = [server_instructions([tool.name for tool in TOOLS])]
    texts += [PACKAGED.joinpath(rel).read_text(encoding="utf-8") for rel in packaged_rels()]
    for text in texts:
        for uri in re.findall(r"jev-skill://\S+", text):
            uri = uri.rstrip("`.,:;)")
            assert uri in served, f"served text names {uri!r}, which resources/list does not serve"


def test_packaged_skill_descriptions_fit_the_agent_skills_limit() -> None:
    for skill in sorted(PACKAGED.glob("*/SKILL.md")):
        front = skill.read_text(encoding="utf-8").split("---", 2)[1]
        line = next(row for row in front.splitlines() if row.startswith("description:"))
        length = len(line.removeprefix("description:").strip())
        assert 1 <= length <= 1024, (skill, length)


def test_on_demand_sentence_has_one_owner() -> None:
    first, rest = ON_DEMAND.split(". ", 1)
    sentences = (first + ".", rest.split(". ", 1)[0] + ".")
    copies = (
        (ROOT / "docs" / "agent-rules.md").read_text(encoding="utf-8"),
        (ROOT / "README.md").read_text(encoding="utf-8"),
        server_instructions([tool.name for tool in TOOLS]),
        (PACKAGED / "jev-mcp" / "SKILL.md").read_text(encoding="utf-8"),
    )
    for sentence in sentences:
        assert sentence in ON_DEMAND
        for text in copies:
            assert sentence in text


def test_stdio_serves_the_allowlist_and_names_both_skills() -> None:
    with StdioServer() as server:
        initialized = server.initialize()
        listed = server.request({"jsonrpc": "2.0", "id": 2, "method": "resources/list", "params": {}})
        jev = server.request({"jsonrpc": "2.0", "id": 3, "method": "resources/read", "params": {"uri": JEV}})
        routing = server.request({"jsonrpc": "2.0", "id": 4, "method": "resources/read", "params": {"uri": JEV_MCP}})
        prompts = server.request({"jsonrpc": "2.0", "id": 5, "method": "prompts/list", "params": {}})
        jev_prompt = server.request({"jsonrpc": "2.0", "id": 6, "method": "prompts/get", "params": {"name": "jev"}})
        routing_prompt = server.request(
            {"jsonrpc": "2.0", "id": 7, "method": "prompts/get", "params": {"name": "jev-mcp"}}
        )
        server.close_stdin()
        server.wait()

    result = _result(initialized)
    instructions = result["instructions"]
    assert isinstance(instructions, str)
    assert instructions == server_instructions([tool.name for tool in TOOLS])
    raw_capabilities = result["capabilities"]
    assert isinstance(raw_capabilities, dict)
    capabilities = cast(dict[str, Any], raw_capabilities)
    assert set(capabilities) == _CAPABILITIES
    assert "mcp__" not in instructions

    resources = _records(_result(listed)["resources"])
    served = {str(item["uri"]) for item in resources}
    assert served == {uri_for(rel) for rel in packaged_rels()}
    assert JEV in served and JEV_MCP in served

    jev_text = _text(jev, JEV)
    routing_text = _text(routing, JEV_MCP)
    assert jev_text == (PACKAGED / "jev" / "SKILL.md").read_text(encoding="utf-8")
    assert routing_text == (PACKAGED / "jev-mcp" / "SKILL.md").read_text(encoding="utf-8")
    assert routing_text == (ROOT / "docs" / "skills" / "jev-mcp" / "SKILL.md").read_text(encoding="utf-8")

    listed_prompts = _records(_result(prompts)["prompts"])
    assert {str(item["name"]) for item in listed_prompts} == {"jev", "jev-mcp"}
    assert _prompt_text(jev_prompt) == jev_text
    assert _prompt_text(routing_prompt) == routing_text
