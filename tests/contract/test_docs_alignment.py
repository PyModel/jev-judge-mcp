"""Doc-alignment invariants: prose the code has outgrown fails here, not in a later audit.

The divergence registry's integrity gate (ADR-0020) guards registry → code. This guards the
directions that rotted in practice: ADR ids (unique, and every `ADR-NNNN` citation resolves),
numeric tool-count claims in prose, the installer's hand-copied tool order, divergence ids
named by source, and the caps/threshold tables on `docs/reference/limits.md` (a value there
that drifts from `limits.py` or `policy/thresholds.py` is a reader-facing lie, so the page and
the code move in one change). A failure here means a doc or a copy drifted — fix the doc or the
copy, never the assertion.
"""

import json
import re
from dataclasses import fields
from pathlib import Path

from jev_judge_mcp import limits
from jev_judge_mcp.install.verify import EXPECTED_TOOLS
from jev_judge_mcp.policy import thresholds as policy_thresholds
from jev_judge_mcp.tools import TOOLS

ROOT = Path(__file__).parents[2]
ADR_DIR = ROOT / "docs" / "adr"

_PRUNED = {
    ".git",
    ".venv",
    ".hypothesis",
    ".pytest_cache",
    ".ruff_cache",
    ".treehouse",
    ".benchmarks",
    "__pycache__",
    "node_modules",
    "dist",
    "blackbox",
}
_TEXT_SUFFIXES = {".md", ".py", ".json", ".toml", ".yml", ".jsonl"}

_COUNT_WORDS = {"ten": 10, "eleven": 11, "twelve": 12}
# "ten tools", "the ten tools", "Ten JevTools", "10/10 tools": a claim that the tool count is N.
# "the reference's ten, plus an extension" is not a count claim and must not match.
_COUNT_CLAIM = re.compile(
    r"\bthe (ten|eleven|twelve) tools\b"
    r"|\b(ten|eleven|twelve) (?:published |judgment )?tools\b"
    r"|\bTen JevTools\b"
    r"|\b(\d+)/\3 tools\b"
)
_CITED_ADR = re.compile("ADR" + r"-(\d{4})")
_NAMED_DIVERGENCE = re.compile(r"divergence `([a-z0-9-]+)`")

CLAIM_DOCS = ("README.md", "docs/ROADMAP.md", "AGENTS.md", "docs/CONTEXT.md", "SECURITY.md", "CONTRIBUTING.md")


def _text_files() -> list[Path]:
    return [
        path
        for path in ROOT.rglob("*")
        if path.is_file() and path.suffix.lower() in _TEXT_SUFFIXES and not any(part in _PRUNED for part in path.parts)
    ]


def _adr_numbers() -> dict[str, list[str]]:
    numbers: dict[str, list[str]] = {}
    for path in ADR_DIR.glob("????-*.md"):
        numbers.setdefault(path.name[:4], []).append(path.name)
    return numbers


def test_adr_numbers_are_unique() -> None:
    duplicates = {number: names for number, names in _adr_numbers().items() if len(names) > 1}
    assert duplicates == {}


def test_every_adr_citation_resolves() -> None:
    numbers = _adr_numbers()
    cited: set[str] = set()
    for path in _text_files():
        cited |= set(_CITED_ADR.findall(path.read_text(encoding="utf-8", errors="replace")))
    unresolved = sorted(number for number in cited if number not in numbers)
    assert unresolved == []


def test_the_installer_expects_the_published_registry() -> None:
    assert tuple(EXPECTED_TOOLS) == tuple(tool.name for tool in TOOLS)


def test_numeric_tool_count_claims_match_the_registry() -> None:
    # AGENTS.md is a gitignored per-machine file (like CLAUDE.md): present in the primary checkout,
    # absent in worktrees. Every other claim doc is tracked and stays required.
    claim_docs = [ROOT / name for name in CLAIM_DOCS if name != "AGENTS.md" or (ROOT / "AGENTS.md").exists()]
    checked = [
        *claim_docs,
        *(ROOT / "docs" / "harness").glob("*.md"),
        ROOT / "docs" / "skills" / "jev-mcp" / "SKILL.md",
    ]
    wrong: list[tuple[Path, str]] = []
    for path in checked:
        for match in _COUNT_CLAIM.finditer(path.read_text(encoding="utf-8")):
            word = next(group for group in match.groups() if group is not None and group.isalpha())
            if _COUNT_WORDS[word] != len(TOOLS):
                wrong.append((path, match.group(0)))
    assert wrong == []


def test_divergence_ids_named_by_source_are_registered() -> None:
    registry = json.loads((ROOT / "docs" / "reference" / "divergences.json").read_text(encoding="utf-8"))
    registered = {entry["id"] for entry in registry["divergences"]}
    named: set[str] = set()
    for path in (ROOT / "src").rglob("*.py"):
        named |= set(_NAMED_DIVERGENCE.findall(path.read_text(encoding="utf-8")))
    assert named <= registered


_LIMITS_PAGE = ROOT / "docs" / "reference" / "limits.md"

_TOOL_CAPS = {
    "jev_verify": limits.VERIFY,
    "jev_screen": limits.SCREEN,
    "jev_find": limits.FIND,
    "jev_classify": limits.CLASSIFY,
    "jev_decide": limits.DECIDE,
    "jev_rerank": limits.RERANK,
    "jev_compare": limits.COMPARE,
    "jev_extract": limits.EXTRACT,
    "jev_review": limits.REVIEW,
    "jev_gate": limits.GATE,
    "jev_score": limits.SCORE,
}
_THRESHOLDS_SECTION = "Defaults and thresholds"
_SHARED_SECTION = "shared"
_TABLE_ROW = re.compile(r"^\|\s*`([A-Za-z_]+)`\s*\|([^|]*)\|")
_HEADING_3 = re.compile(r"^### (\w+)")
_HEADING_2 = re.compile(r"^## ([^#].*)")


def _render(value: object) -> str:
    """The page's canonical cell for a cap or default: plain digits, `no cap` for null."""
    if value is None:
        return "no cap"
    if isinstance(value, float):
        return repr(value)
    return str(value)


def _page_sections() -> dict[str, dict[str, str]]:
    """Every table row of the limits page, bucketed under its nearest ### (or ##) heading."""
    sections: dict[str, dict[str, str]] = {}
    section, subsection = None, None
    for line in _LIMITS_PAGE.read_text(encoding="utf-8").splitlines():
        if match := _HEADING_3.match(line):
            subsection = match.group(1)
            continue
        if match := _HEADING_2.match(line):
            section, subsection = match.group(1).strip(), None
            continue
        if (row := _TABLE_ROW.match(line)) and (bucket := subsection or section):
            sections.setdefault(bucket, {})[row.group(1)] = row.group(2).strip()
    return sections


def _assert_rows(section: str, expected: dict[str, str], where: str) -> None:
    rows = _page_sections().get(section, {})
    missing = sorted(set(expected) - set(rows))
    assert not missing, f"{where}: {section} is missing rows for {missing}"
    wrong = {name: (expected[name], rows[name]) for name in expected if rows[name] != expected[name]}
    assert not wrong, f"{where}: values drifted from the code (expected, stated): {wrong}"


def test_limits_page_states_every_frozen_cap() -> None:
    """`docs/reference/limits.md` carries every `limits.py` cap, exactly (ADR-0014: the page is
    the caller-facing copy of the freeze, so a re-freeze that skips it fails here)."""
    for tool, caps in _TOOL_CAPS.items():
        expected = {field.name: _render(getattr(caps, field.name)) for field in fields(caps)}
        _assert_rows(tool, expected, "limits page")
    shared = {field.name: _render(getattr(limits.CANDIDATES, field.name)) for field in fields(limits.CANDIDATES)}
    shared["SANITIZE_ID_UNITS"] = _render(limits.SANITIZE_ID_UNITS)
    _assert_rows(_SHARED_SECTION, shared, "limits page")


def test_limits_page_states_every_frozen_threshold() -> None:
    """The page's threshold table matches `policy/thresholds.py` constant for constant, so a
    changed default without its doc row (and `policy_version` bump) fails here."""
    expected = {
        name: _render(value)
        for name, value in vars(policy_thresholds).items()
        if name.isupper() and isinstance(value, (int, float)) and not isinstance(value, bool)
    }
    assert expected, "policy_thresholds lost its numeric constants; update the limits page owner"
    _assert_rows(_THRESHOLDS_SECTION, expected, "limits page")
