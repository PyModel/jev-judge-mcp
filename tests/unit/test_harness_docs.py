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
    completion = json.loads((HARNESS / "completion.hooks.json").read_text(encoding="utf-8"))
    completion_matcher = completion["hooks"]["PreToolUse"][0]["matcher"]
    assert completion_matcher == "Bash"
    screen = json.loads((HARNESS / "screen.hooks.json").read_text(encoding="utf-8"))
    screen_group = screen["hooks"]["PostToolUse"][0]
    screen_command = screen_group["hooks"][0]
    assert screen_group["matcher"] == "Bash|Read|WebFetch|WebSearch"
    assert screen_command["type"] == "command"
    assert screen_command["timeout"] == 30
    assert screen_command["command"] == "/absolute/path/to/jev-judge-mcp hook screen"


def test_compact_hooks_sample_parses() -> None:
    """The cut point rides SessionStart(compact), the one documented injection path (ADR-0077)."""
    payload = json.loads((HARNESS / "compact.hooks.json").read_text(encoding="utf-8"))
    entry = payload["hooks"]["SessionStart"][0]
    command = entry["hooks"][0]
    assert entry["matcher"] == "compact"
    assert command["type"] == "command"
    assert command["timeout"] == 30
    assert command["command"] == "/absolute/path/to/jev-judge-mcp hook compact-cut"


def test_harness_tree_has_no_home_path_or_key_marker() -> None:
    files = sorted(path for path in HARNESS.rglob("*") if path.is_file())
    assert {path.name for path in files} == {
        "claude.md",
        "codex.md",
        "pi.md",
        "hooks.md",
        "gate.hooks.json",
        "completion.hooks.json",
        "screen.hooks.json",
        "compact.hooks.json",
    }
    for path in files:
        text = path.read_text(encoding="utf-8")
        for marker in FORBIDDEN:
            assert marker not in text, f"{path.relative_to(ROOT)} contains {marker!r}"


def test_each_harness_page_keeps_the_hook_contract() -> None:
    for name in PAGES:
        text = (HARNESS / name).read_text(encoding="utf-8")
        assert "(hooks.md" in text
        assert "JEV_GATE_STATE" not in text
        assert "uv run --directory" not in text
        assert "JEV_GATE_THRESHOLD" not in text
    contract = (HARNESS / "hooks.md").read_text(encoding="utf-8")
    for marker in ("opt-in", "never allow", "JEV_GATE_STATE", "does not merge", "does not enable", "ADR-0035"):
        assert marker in contract
    assert "The command hook is not the `jev_gate` MCP tool, which stays the completion gate (ADR-0035)." in contract
    assert "JEV_GATE_THRESHOLD" not in contract
    assert contract.count("uv run --directory") == 1


def test_each_harness_page_states_the_duplicate_id_rule() -> None:
    """Each client links to the published ID rules at their common owner."""
    for name in PAGES:
        text = (HARNESS / name).read_text(encoding="utf-8")
        assert "(../tools.md#caller-ids)" in text
        assert "Collisions after sanitization return" not in text
    rules = (ROOT / "docs/tools.md").read_text().split("## Caller IDs", 1)[1].split("\n## ", 1)[0]
    assert "invalid_arguments" in rules
    assert "ASCII letters, digits, dots, hyphens, and underscores" in rules
    assert "Two supplied IDs with the same sanitized value" in rules


def test_readme_links_harness_docs() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    for name in PAGES:
        assert f"docs/harness/{name})" in readme


def test_shipped_hook_fragments_do_not_change_directory() -> None:
    """``uv run --directory`` would make gate judge the checkout, not the caller's repo."""
    for name in ("gate.hooks.json", "completion.hooks.json", "screen.hooks.json", "compact.hooks.json"):
        payload = json.loads((HARNESS / name).read_text(encoding="utf-8"))
        for groups in payload["hooks"].values():
            for group in groups:
                for hook in group["hooks"]:
                    assert "--directory" not in hook["command"].split(), name
