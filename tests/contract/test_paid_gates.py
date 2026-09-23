"""Every paid runner's flag stays off every default path (ADR-0027): no Makefile line outside a comment
and no CI workflow names it, no marker expression but `-m live` admits a `live` test, and neither `ci:`
nor a workflow runs a paid target. Each runner keeps its own refusal test next to it."""

import re
import shlex
from collections.abc import Sequence
from pathlib import Path

import pytest
from _pytest.mark.expression import Expression

from evals.ab import run as ab_run
from evals.bench import run as bench_run
from evals.runners import live

REPO = Path(__file__).resolve().parents[2]


PAID_FLAGS = (live.LIVE_FLAG, ab_run.LIVE_FLAG, bench_run.LIVE_FLAG)
PAID_TARGETS = ("eval-live", "security-live", "ab")
LIVE_MARKER = "live"


def _makefile() -> str:
    return (REPO / "Makefile").read_text(encoding="utf-8")


def _workflows() -> list[str]:
    return [p.read_text(encoding="utf-8") for p in sorted((REPO / ".github" / "workflows").glob("*.y*ml"))]


def flag_setters(makefile: str, workflows: Sequence[str], flag: str) -> list[str]:
    """Every Makefile line outside a comment, and every workflow line, that names `flag`.

    Comment lines are skipped on purpose: the `ab` target's comment tells the caller to pass the flag.
    A Makefile without a recipe or an empty workflow set is an error: it would pass vacuously."""
    if not any(line.startswith("\t") and line.strip() for line in makefile.splitlines()):
        raise ValueError("the Makefile has no recipe lines")
    if not workflows:
        raise ValueError("no CI workflows to check")
    lines = [line for line in makefile.splitlines() if not line.lstrip().startswith("#")]
    lines += [line for text in workflows for line in text.splitlines()]
    return [line for line in lines if flag in line]


@pytest.mark.parametrize("flag", PAID_FLAGS)
def test_no_make_target_or_ci_workflow_sets_a_paid_flag(flag: str) -> None:
    assert flag_setters(_makefile(), _workflows(), flag) == []


@pytest.mark.parametrize("flag", PAID_FLAGS)
@pytest.mark.parametrize(
    "makefile",
    [
        "ab:\n\t{flag}=1 uv run python -m x\n",
        "export {flag}=1\nab:\n\tuv run python -m x\n",
        "ab: export {flag} = 1\nab:\n\tuv run python -m x\n",
    ],
)
def test_the_guard_catches_a_make_file_that_sets_the_flag(flag: str, makefile: str) -> None:
    assert flag_setters(makefile.format(flag=flag), ["on: push"], flag)


@pytest.mark.parametrize("flag", PAID_FLAGS)
def test_the_guard_catches_a_workflow_and_ignores_a_comment(flag: str) -> None:
    makefile = f"# refuses unless the caller passes {flag}=1\nab:\n\tuv run python -m x\n"
    assert flag_setters(makefile, ["on: push"], flag) == []
    assert flag_setters(makefile, ["on: push", f"env:\n  {flag}: '1'"], flag)


def test_the_guard_refuses_to_pass_vacuously() -> None:
    with pytest.raises(ValueError, match="no recipe lines"):
        flag_setters("", ["on: push"], live.LIVE_FLAG)
    with pytest.raises(ValueError, match="no recipe lines"):
        flag_setters("# only a comment\nall:\n", ["on: push"], live.LIVE_FLAG)
    with pytest.raises(ValueError, match="no CI workflows"):
        flag_setters("all:\n\ttrue\n", [], live.LIVE_FLAG)


def marker_expressions(makefile: str) -> list[str]:
    """The `-m` expression of every pytest recipe line. Other commands' `-m` (`python -m pkg`) is not a
    marker. A Makefile with no marker expression is an error: the check would pass vacuously."""
    expressions: list[str] = []
    for line in makefile.splitlines():
        if not line.startswith("\t") or "$(PYTEST)" not in line:
            continue
        words = shlex.split(line)
        expressions += [words[i + 1] for i, word in enumerate(words[:-1]) if word == "-m"]
    if not expressions:
        raise ValueError("the Makefile runs pytest with no marker expression")
    return expressions


def admits_a_live_test(expression: str) -> bool:
    """Whether `expression` selects a test that carries the `live` marker and no other."""

    def only_live(name: str, /, **_: str | int | bool | None) -> bool:
        return name == LIVE_MARKER

    return Expression.compile(expression).evaluate(only_live)


def test_only_the_live_expression_admits_a_live_test() -> None:
    expressions = marker_expressions(_makefile())
    assert expressions.count(LIVE_MARKER) == 2  # eval-live and security-live; the scan found them
    admitting = [e for e in expressions if e != LIVE_MARKER and admits_a_live_test(e)]
    assert admitting == [], f"a command-line -m replaces addopts; these re-admit `live`: {admitting}"


@pytest.mark.parametrize(
    ("expression", "admits"),
    [
        ("smoke or not smoke", True),
        ("not smoke", True),
        ("live or load", True),
        ("smoke", False),
        ("load", False),
        ("not live", False),
        ("smoke and not live", False),
    ],
)
def test_the_marker_guard_reads_expressions_as_pytest_does(expression: str, admits: bool) -> None:
    assert admits_a_live_test(expression) is admits


def test_the_marker_guard_skips_non_pytest_commands_and_refuses_to_pass_vacuously() -> None:
    makefile = 'ab:\n\tuv run python -m evals.ab.run\nsmoke:\n\t$(PYTEST) tests/integration -m "a or b"\n'
    assert marker_expressions(makefile) == ["a or b"]
    with pytest.raises(ValueError, match="no marker expression"):
        marker_expressions("ab:\n\tuv run python -m evals.ab.run\n")


def paid_target_runs(makefile: str, workflows: Sequence[str]) -> list[str]:
    """Every paid target `ci:` depends on, and every workflow line that runs one with `make`.

    Every paid target must exist in the Makefile, and `ci:` must be declared, or the check is vacuous."""
    targets = {line.split(":", 1)[0] for line in makefile.splitlines() if re.match(r"[\w-]+:", line)}
    missing = [t for t in (*PAID_TARGETS, "ci") if t not in targets]
    if missing:
        raise ValueError(f"the Makefile declares no {', '.join(missing)} target")
    ci = next(line for line in makefile.splitlines() if line.startswith("ci:")).split(":", 1)[1].split()
    runs = [t for t in PAID_TARGETS if t in ci]
    make_paid = re.compile(rf"\bmake\s+(?:\S+\s+)*(?:{'|'.join(map(re.escape, PAID_TARGETS))})(?![\w-])")
    return runs + [line.strip() for text in workflows for line in text.splitlines() if make_paid.search(line)]


def test_neither_ci_nor_a_workflow_runs_a_paid_target() -> None:
    assert paid_target_runs(_makefile(), _workflows()) == []


@pytest.mark.parametrize(
    ("makefile", "workflow", "caught"),
    [
        ("ci: lint eval-live\n", "on: push", ["eval-live"]),
        ("ci: lint\n", "      - run: make security-live", ["- run: make security-live"]),
        ("ci: lint\n", "      - run: make -k ab", ["- run: make -k ab"]),
        ("ci: lint\n", "      - run: make abc build", []),
        ("ci: lint\n", "      - run: make eval", []),
    ],
)
def test_the_ci_guard_catches_a_paid_target(makefile: str, workflow: str, caught: list[str]) -> None:
    declared = "".join(f"{t}:\n\ttrue\n" for t in PAID_TARGETS)
    assert paid_target_runs(makefile + declared, [workflow]) == caught


def test_the_ci_guard_refuses_to_pass_vacuously() -> None:
    with pytest.raises(ValueError, match="no ab target"):
        paid_target_runs("ci: lint\neval-live:\nsecurity-live:\n", ["on: push"])
