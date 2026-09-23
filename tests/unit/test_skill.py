"""The routing skill names only the registered tools, with no price or key marker."""

import re
from pathlib import Path

from jev_judge_mcp.tools import TOOLS

SKILL = Path(__file__).resolve().parents[2] / "docs" / "skills" / "jev-mcp" / "SKILL.md"
_PRICE = re.compile(r"\$\s*\d")
_TOOL_NAME = re.compile(r"jev_[a-z0-9_]+")


def test_routing_skill_names_only_registered_tools() -> None:
    text = SKILL.read_text(encoding="utf-8")
    registered = {tool.name for tool in TOOLS}
    named = set(_TOOL_NAME.findall(text))
    assert named <= registered
    assert registered <= named
    assert "jev_judge" not in text
    assert _PRICE.search(text) is None
    assert "sk-" not in text
