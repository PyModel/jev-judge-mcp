"""Harness samples parse, and they carry no home path or key marker."""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HARNESS = ROOT / "docs" / "harness"
SAMPLE = HARNESS / "gate.hooks.json"
PAGES = ("claude.md", "codex.md", "pi.md")
FORBIDDEN = ("/Users/", "AKIA", "sk-")


def test_gate_hooks_sample_parses() -> None:
    payload = json.loads(SAMPLE.read_text(encoding="utf-8"))
    entry = payload["hooks"]["PreToolUse"][0]
    command = entry["hooks"][0]
    assert entry["matcher"] == "Bash|Write|Edit"
    assert command["type"] == "command"
    assert command["timeout"] == 30
    assert command["command"] == "/absolute/path/to/jev-judge-mcp hook gate"


def test_harness_tree_has_no_home_path_or_key_marker() -> None:
    files = sorted(path for path in HARNESS.rglob("*") if path.is_file())
    assert {path.name for path in files} == {
        "claude.md",
        "codex.md",
        "pi.md",
        "gate.hooks.json",
        "completion.hooks.json",
    }
    for path in files:
        text = path.read_text(encoding="utf-8")
        for marker in FORBIDDEN:
            assert marker not in text, f"{path.relative_to(ROOT)} contains {marker!r}"


def test_each_harness_page_keeps_the_hook_contract() -> None:
    for name in PAGES:
        text = (HARNESS / name).read_text(encoding="utf-8")
        assert "opt-in" in text
        assert "never allow" in text
        assert "JEV_GATE_STATE" in text
        assert "JEV_GATE_THRESHOLD" not in text
        assert "does not merge" in text
        assert "does not enable" in text
        assert "The command hook is not the `jev_gate` MCP tool, which stays the completion gate (ADR-0035)." in text


def test_readme_links_harness_docs() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert readme.count("docs/harness/") == 1
