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
from typing import cast

from jev_judge_mcp import limits
from jev_judge_mcp.calibration.targets import error_budget
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
    extra = sorted(set(rows) - set(expected))
    assert not extra, f"{where}: {section} states rows the code no longer has: {extra}"


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


_TOOL_CARDS = ROOT / "docs" / "tools.md"
_EXAMPLE = ROOT / "examples" / "risk_proportional_thresholds.py"
_LIVE_REPORTS = ROOT / "docs" / "evals" / "live"


def _stated_numbers(text: str, pattern: str) -> tuple[float, ...]:
    """The numbers one prose mention states (one match, every capture group), as floats."""
    matches = re.findall(pattern, text)
    assert len(matches) == 1, f"{pattern!r}: expected exactly one mention, found {len(matches)}"
    captured = matches[0]
    if isinstance(captured, tuple):
        digits = tuple(cast(tuple[str, ...], captured))
    else:
        digits = (str(captured),)
    return tuple(float(digit.replace(",", "")) for digit in digits)


def test_tool_cards_state_the_frozen_numbers() -> None:
    """`docs/tools.md` restates frozen caps, defaults, and targets while explaining behavior. Each
    restated number is pinned to its owner in `limits.py` / `policy.thresholds`, so a re-freeze
    that skips the card fails here — the same shape as the count-claim guard above."""
    text = _TOOL_CARDS.read_text(encoding="utf-8")
    for pattern, expected in [
        (r"clears `auto_accept` \((\d+\.\d+)\)", (policy_thresholds.DEFAULT_CLASSIFY_AUTO_ACCEPT,)),
        (r"clears `minimum_margin` \((\d+\.\d+)\)", (policy_thresholds.DEFAULT_MINIMUM_MARGIN,)),
        (r"hardcoded at (\d+\.\d+)", (policy_thresholds.SCREEN_SUBSTANCE_SKIP_BELOW,)),
        (r"`review_at` \(default (\d+\.\d+)\)", (policy_thresholds.DEFAULT_REVIEW_AT_CAP,)),
        (
            r"answered at ≥ (\d+\.\d+), absent below (\d+\.\d+)",
            (policy_thresholds.EXISTS_FOUND_AT, policy_thresholds.EXISTS_ABSENT_BELOW),
        ),
        (
            r"certifying the (\d+(?:\.\d+)?)%\s+target",
            (error_budget("jev_gate") * 100,),
        ),
        (r"between (\d+) and (\d+) options", (limits.DECIDE.candidates_min, limits.DECIDE.candidates_max)),
        (
            r"capped at (\d+), each up to (\d+) UTF-16",
            (limits.DECIDE.requirements_max, limits.DECIDE.requirement_max),
        ),
        (r"`top_k` defaults to (\d+)", (limits.FIND.top_k_default,)),
        (r"over ([\d,]+)\s+UTF-16 units refuses", (limits.RERANK.aggregate_candidate_units,)),
        (r"per aspect \(up to (\d+)\)", (limits.COMPARE.aspects_max,)),
        (r"above ([\d,]+) UTF-16 units each", (limits.COMPARE.passage_max,)),
        (
            r"capped at (\d+) items and ([\d,]+) aggregate",
            (limits.GATE.evidence_items, limits.GATE.aggregate_evidence_units),
        ),
        (r"scale of (\d+) to (\d+) levels", (limits.SCORE.levels_min, limits.SCORE.levels_max)),
    ]:
        assert _stated_numbers(text, pattern) == expected, pattern
    # The screen card's single "hardcoded at" number states both skip thresholds at once.
    assert policy_thresholds.SCREEN_SUBSTANCE_SKIP_BELOW == policy_thresholds.SCREEN_RELEVANCE_SKIP_BELOW


def test_tool_cards_state_the_recorded_results() -> None:
    """The two Measured paragraphs quote the recorded L3 run; each n and primary-metric value is
    pinned to the recorded report JSON under `docs/evals/live/`, the machine-readable source."""
    text = _TOOL_CARDS.read_text(encoding="utf-8")
    reports = sorted(_LIVE_REPORTS.glob("*.report.json"))
    assert reports, "docs/evals/live lost its recorded reports; the tool cards quote them"
    for path in reports:
        report = json.loads(path.read_text(encoding="utf-8"))
        primary = str(report["primary"])
        pattern = rf"n=(\d+) (?:claims|items), {re.escape(primary)} (\d+\.\d+)"
        assert _stated_numbers(text, pattern) == (
            float(report["n"]),
            float(report["metrics"][primary]),
        ), path.name


def test_example_docstring_states_the_frozen_defaults() -> None:
    """The worked example names the server's default bars in its docstring; pinned like the cards."""
    text = _EXAMPLE.read_text(encoding="utf-8")
    assert _stated_numbers(text, r"default (\d+\.\d+) for\s+jev_verify") == (policy_thresholds.DEFAULT_AUTO_ACCEPT,)
    assert _stated_numbers(text, r"; (\d+\.\d+) for\s+jev_classify") == (
        policy_thresholds.DEFAULT_CLASSIFY_AUTO_ACCEPT,
    )
