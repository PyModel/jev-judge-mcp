"""Doc-alignment invariants: prose the code has outgrown fails here, not in a later audit.

The divergence registry's integrity gate (ADR-0020) guards registry → code. This guards the
directions that rotted in practice: ADR ids (unique, and every `ADR-NNNN` citation resolves),
numeric tool-count claims in prose, the installer's hand-copied tool order, divergence ids
named by source, and the caps/threshold tables on `docs/reference/limits.md` (a value there
that drifts from `limits.py` or `policy/thresholds.py` is a reader-facing lie, so the page and
the code move in one change). The same rule pins the README's measured-result claims to the
tracked reports that own them, the agent rule block to the caps in `limits.py`, and the set-up
prompt to the real CLI surface: README, rule block, and code move in one change. A failure here
means a doc or a copy drifted — fix the doc or the copy, never the assertion.
"""

import json
import re
import subprocess
import sys
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


# --- Measured results: README ↔ tracked reports -------------------------------------------------

# Headline numbers the README states and the report that owns each, as (claim, exact README
# occurrences[, report's own string when it differs]. The report owns the number (presence); the
# README count is pinned so a drifted duplicate, a dropped restatement, or a silently softened
# number fails: README and report move in one change. The unfavorable agent-outcome figures are
# pinned on the same terms as the favorable ones.
_MEASURED_CLAIMS: dict[str, tuple[tuple[str, int] | tuple[str, int, str], ...]] = {
    "evals/reports/bench150.md": (
        ("464.6 ms", 2),
        ("1245.3 ms", 1),
        ("1468.8 ms", 1),
        ("157", 1),
        ("10.4 s", 1, "10.43"),
        ("3.06", 1),
        ("2.91", 1),
        ("13.95", 1),
        ("0.0060", 1),
        ("$0.0928", 1),
        ("$0.0955", 1),
        ("$0.2904", 1),
    ),
    "evals/reports/jevbench-public.md": (
        ("89/92", 2),
        ("36/36", 2),
        ("17/20", 1),
        ("86/86", 1),
        ("$0.002281", 1),
        ("54,308", 1),
        ("$0.025 per 1,000 decisions", 2),
        ("jev-1.13.0", 2),
    ),
    "docs/evals/README.md": (
        ("| 6/9 / 6/9 |", 1),
        ("| 6/8 / 6/8 |", 1),
        ("14.6 s", 1),
        ("18.3 s", 1),
        ("49.4 s", 1),
        ("127.8 s", 1),
        ("+4.6 s", 1),
        ("+85.8 s", 1),
        ("$1.4953", 1),
        ("$0.0006", 1),
        ("slower with Jev", 3),
    ),
}


def test_measured_result_claims_match_their_reports() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    for report_name, claims in _MEASURED_CLAIMS.items():
        report = (ROOT / report_name).read_text(encoding="utf-8")
        for needle, times, *owned in claims:
            source = owned[0] if owned else needle
            assert source in report, f"{report_name} no longer states {source!r}: rerun moved the report"
            found = readme.count(needle)
            assert found == times, (
                f"README states {needle!r} {found}x, pinned at {times}x (owned by {report_name}): "
                "a restatement drifted, a number was softened, or a pin needs updating with the report"
            )


# --- Agent rule block and set-up prompt -----------------------------------------------------------

_RULES_FILE = ROOT / "docs" / "agent-rules.md"
_FENCE = re.compile(r"```(\w*)\n(.*?)\n```", re.DOTALL)
_JEV_TOOL = re.compile(r"\b(jev_[a-z_]+)\b")
_PROMPT_FLAG = re.compile(r"(?<![\w-])(--?[A-Za-z][\w-]*)")
_UVX_FLAGS = {"--from"}
"""Flags the prompt places on `uvx` itself; every other flag must exist on this repo's CLI."""


def _readme_fences(info: str) -> list[str]:
    return [body for tag, body in _FENCE.findall((ROOT / "README.md").read_text(encoding="utf-8")) if tag == info]


def _rules_row(rules: str, tool: str) -> str:
    prefix = f"| `{tool}` |"
    return next((line for line in rules.splitlines() if line.startswith(prefix)), "")


def test_agent_rule_block_copy_in_the_readme_is_the_tracked_file() -> None:
    """The README's rule block equals docs/agent-rules.md exactly, so the two never drift."""
    rules = _RULES_FILE.read_text(encoding="utf-8").strip()
    assert any(body.strip() == rules for body in _readme_fences("markdown")), (
        "the README ```markdown block no longer matches docs/agent-rules.md; move both in one change"
    )


def test_agent_docs_name_only_registered_tools() -> None:
    registered = {tool.name for tool in TOOLS}
    for path in (ROOT / "README.md", _RULES_FILE):
        unknown = set(_JEV_TOOL.findall(path.read_text(encoding="utf-8"))) - registered
        assert not unknown, f"{path.name} names tools that are not registered: {sorted(unknown)}"


def _cap_needles(cap: int | None, unit: str) -> list[str]:
    """The rule block's cell for a cap, whichever way limits.py freezes it: bounded or open."""
    return [f"\u2264{cap} {unit}"] if cap is not None else ["no length bound"]


def test_agent_rule_block_caps_match_limits() -> None:
    """Every cap the rule block states derives from limits.py, both ways: a re-frozen bound must
    appear, and stale "no length bound" text must go."""
    rules = _RULES_FILE.read_text(encoding="utf-8")
    expected: dict[str, list[str]] = {
        "jev_verify": _cap_needles(limits.VERIFY.claims_max, "claims"),
        "jev_gate": [
            f"\u2264{limits.GATE.claims_max} claims",
            f"\u2264{limits.GATE.evidence_items} evidence items",
            f"{limits.GATE.aggregate_evidence_units:,} units",
            f"{limits.GATE.doc_units:,} units",
        ],
        "jev_review": [f"{limits.REVIEW.doc_units:,} units"],
        "jev_screen": _cap_needles(limits.SCREEN.text_max, "units"),
        "jev_compare": [f"{limits.COMPARE.passage_max:,} units", f"\u2264{limits.COMPARE.aspects_max} aspects"],
        "jev_find": [f"\u2264{limits.CANDIDATES.max_items} candidates", f"{limits.CANDIDATES.text_units:,} units"],
        "jev_rerank": [f"\u2264{limits.CANDIDATES.max_items} candidates"],
        "jev_classify": [f"\u2264{limits.CLASSIFY.items_max} items", f"\u2264{limits.CLASSIFY.classes_max} classes"],
        "jev_decide": [f"{limits.DECIDE.candidates_min}\u2013{limits.DECIDE.candidates_max} options"],
        "jev_extract": [f"{limits.EXTRACT.document_max:,} units", f"\u2264{limits.EXTRACT.fields_max} fields"],
        "jev_score": [f"{limits.SCORE.levels_min}\u2013{limits.SCORE.levels_max} levels"],
    }
    missing = [
        f"{tool} row lacks {needle!r}"
        for tool, needles in expected.items()
        for needle in needles
        if needle not in _rules_row(rules, tool)
    ]
    stale_unbounded = [
        f"{tool} row still says 'no length bound' but limits.py bounds it"
        for tool, needles in expected.items()
        if "no length bound" not in needles and "no length bound" in _rules_row(rules, tool)
    ]
    assert missing == [] and stale_unbounded == [], (
        f"docs/agent-rules.md caps drifted from limits.py: {missing + stale_unbounded}"
    )


def test_setup_prompt_names_only_real_cli_surface() -> None:
    """The copy-paste prompt runs only real subcommands and flags, and lists every terminal
    install target: a new TARGETS entry fails here until the prompt names it."""
    from jev_judge_mcp.install.engine import TARGETS

    prompt = next((body for body in _readme_fences("text") if "jev-judge-mcp setup" in body), "")
    assert prompt, "the set-up prompt block is missing from the README"
    dry_run = prompt.find("--dry-run")
    confirmation = prompt.find("confirmation")
    install = prompt.find("install -a")
    assert 0 <= dry_run < confirmation < install, (
        "the prompt must dry-run, wait for the user's chat confirmation, and only then install"
    )
    assert "install -a <your agent> -y" in prompt, (
        "the real install command must carry -y: an agent shell is not a TTY, so the CLI prompt cannot fire"
    )
    assert "pi install npm:pi-mcp-adapter" in prompt, "the prompt omits the Pi MCP adapter prerequisite"
    for marker in ("TYPESAFE_API_KEY", "restart", "CLAUDE.md", "AGENTS.md", "docs/agent-rules.md"):
        assert marker in prompt, f"the set-up prompt no longer covers {marker}"
    for agent in TARGETS:
        if agent != "claude-desktop":
            assert agent in prompt, f"the set-up prompt does not list install target {agent!r}"
    help_text = subprocess.run(
        [sys.executable, "-m", "jev_judge_mcp", "--help"], capture_output=True, text=True, check=False
    ).stdout
    install_help = subprocess.run(
        [sys.executable, "-m", "jev_judge_mcp", "install", "--help"],
        capture_output=True,
        text=True,
        check=False,
    ).stdout
    listed = set(re.findall(r"^  (\S+)", help_text, re.MULTILINE))
    for subcommand in set(re.findall(r"`[^`]*jev-judge-mcp (\w+)[^`]*`", prompt)):
        assert subcommand in listed, f"the prompt runs `jev-judge-mcp {subcommand}`, which the CLI usage does not list"
    for flag in set(_PROMPT_FLAG.findall(prompt)):
        assert flag in install_help or flag in _UVX_FLAGS, f"the prompt uses flag {flag}, which no CLI here defines"
