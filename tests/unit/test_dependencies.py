"""The third-party `regex` module stays out: stdlib `re` is the ADR-0004 engine (ADR-0018)."""

import ast
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def requirement_name(requirement: str) -> str:
    match = re.match(r"[A-Za-z0-9._-]+", requirement)
    assert match, requirement
    return match.group().lower().replace("_", "-")


def test_regex_is_not_a_dependency() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    requirements = list(project["dependencies"])
    for extra in project.get("optional-dependencies", {}).values():
        requirements.extend(extra)
    assert "regex" not in {requirement_name(requirement) for requirement in requirements}


def test_src_does_not_import_regex() -> None:
    offenders: list[str] = []
    for path in sorted((ROOT / "src").rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module]
            else:
                continue
            if any(name.split(".")[0] == "regex" for name in names):
                offenders.append(f"{path.relative_to(ROOT)}:{node.lineno}")
    assert offenders == []


def test_roadmap_mcp_row_quotes_the_pyproject_pin() -> None:
    """The ROADMAP dependency row states the pin pyproject enforces; the mcp row once said `<3`.

    pyproject caps `mcp` at the tested minor for the server's private-internals use, so a ROADMAP
    row promising the whole `<3` range invites an upgrade the pin refuses.
    """
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    pin = next(requirement for requirement in project["dependencies"] if requirement_name(requirement) == "mcp")
    row = next(
        line
        for line in (ROOT / "docs/ROADMAP.md").read_text(encoding="utf-8").splitlines()
        if line.startswith("| MCP |")
    )
    assert f"`{pin}`" in row
