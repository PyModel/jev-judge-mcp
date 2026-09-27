"""A connecting client can fetch both skills, and the two roles stay distinct (ADR-0071).

The owner is the stdio wire. The wheel path is the uvx smoke test: this one cannot see a
package that dropped the files, and that one cannot see a checkout-relative read.
"""

from pathlib import Path
from typing import Any, cast

from tests.support.stdio import StdioServer

ROOT = Path(__file__).resolve().parents[2]
PACKAGED = ROOT / "src" / "jev_judge_mcp" / "skills"
JEV = "jev-skill://jev/SKILL.md"
JEV_MCP = "jev-skill://jev-mcp/SKILL.md"
PROVENANCE = "jev-skill://jev/PROVENANCE.md"


def _files() -> set[str]:
    found: set[str] = set()
    for path in PACKAGED.rglob("*"):
        if not path.is_file() or path.name == "__init__.py" or "__pycache__" in path.parts:
            continue
        rel = path.relative_to(PACKAGED).as_posix()
        if rel.startswith(("jev/", "jev-mcp/")):
            found.add(f"jev-skill://{rel}")
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


def test_stdio_serves_both_skills_and_names_their_roles() -> None:
    with StdioServer() as server:
        initialized = server.initialize()
        listed = server.request({"jsonrpc": "2.0", "id": 2, "method": "resources/list", "params": {}})
        jev = server.request({"jsonrpc": "2.0", "id": 3, "method": "resources/read", "params": {"uri": JEV}})
        routing = server.request({"jsonrpc": "2.0", "id": 4, "method": "resources/read", "params": {"uri": JEV_MCP}})
        provenance = server.request(
            {"jsonrpc": "2.0", "id": 5, "method": "resources/read", "params": {"uri": PROVENANCE}}
        )
        prompts = server.request({"jsonrpc": "2.0", "id": 6, "method": "prompts/list", "params": {}})
        jev_prompt = server.request({"jsonrpc": "2.0", "id": 7, "method": "prompts/get", "params": {"name": "jev"}})
        routing_prompt = server.request(
            {"jsonrpc": "2.0", "id": 8, "method": "prompts/get", "params": {"name": "jev-mcp"}}
        )
        server.close_stdin()
        server.wait()

    instructions = _result(initialized)["instructions"]
    assert isinstance(instructions, str)
    assert JEV in instructions
    assert JEV_MCP in instructions
    assert PROVENANCE in instructions
    assert "not for calling these tools" in instructions
    assert "which of these tools fits a step" in instructions
    assert "Jev is on demand only" in instructions
    assert "never route every judgment through it" in instructions
    assert "before a done claim, jev_gate" in instructions
    assert "jev_screen" in instructions
    assert "jev_verify" in instructions
    assert "cheap to reverse" in instructions
    assert "mcp__" not in instructions

    resources = _records(_result(listed)["resources"])
    served = {str(item["uri"]) for item in resources}
    assert served == _files()
    assert {JEV, JEV_MCP, PROVENANCE} <= served
    by_uri = {str(item["uri"]): str(item["description"]) for item in resources}
    assert "never route every judgment through it" in by_uri[JEV]
    assert "never route every judgment through it" in by_uri[JEV_MCP]

    jev_text = _text(jev, JEV)
    routing_text = _text(routing, JEV_MCP)
    assert jev_text == (PACKAGED / "jev" / "SKILL.md").read_text(encoding="utf-8")
    assert routing_text == (PACKAGED / "jev-mcp" / "SKILL.md").read_text(encoding="utf-8")
    assert routing_text == (ROOT / "docs" / "skills" / "jev-mcp" / "SKILL.md").read_text(encoding="utf-8")
    assert "name: jev\n" in jev_text
    assert "name: jev-mcp\n" in routing_text
    assert "not for calling these tools" not in jev_text
    assert "Do not copy its cookbook thresholds" in routing_text
    assert "never route every judgment through it" in routing_text
    assert "before a done claim, `jev_gate`" in routing_text
    provenance_text = _text(provenance, PROVENANCE)
    assert "c1802b24bb94" in provenance_text
    assert "whitespace" in provenance_text
    assert "never route every judgment through it" in provenance_text

    listed_prompts = _records(_result(prompts)["prompts"])
    names = {str(item["name"]): str(item["description"]) for item in listed_prompts}
    assert set(names) == {"jev", "jev-mcp"}
    assert "never route every judgment through it" in names["jev"]
    assert "never route every judgment through it" in names["jev-mcp"]
    assert _prompt_text(jev_prompt) == jev_text
    assert _prompt_text(routing_prompt) == routing_text


def _prompt_text(reply: dict[str, Any]) -> str:
    messages = _records(_result(reply)["messages"])
    assert messages
    content = messages[0]["content"]
    assert isinstance(content, dict)
    text = cast(dict[str, Any], content)["text"]
    assert isinstance(text, str)
    return text
