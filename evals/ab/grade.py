"""Deterministic grading: completion, regressions, and test pass rate all come from one unittest run.

The agent's tree is copied, its `tests/` is replaced by the pristine snapshot tests plus the hidden
acceptance tests, and every test id is recorded. The expected ids come from grading the task's
reference solution, so a module that fails to import counts all of its tests as failed.

Task correctness is separate from test-file edits (ADR-0073). Adding a test function, or a new test
file, does not fail the run. Modifying, deleting, skipping, or weakening a pre-existing test does.
Added tests are run against the agent's own tree and must pass; an irrelevant added test is recorded,
not failed. `Grade.correct` is that tree verdict. Whether the stated decision matches gold is
`run_correct`, applied when the run record is built.
"""

import ast
import hashlib
import json
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from evals.ab.tasks import SNAPSHOT, Task, protected_files

ACCEPTANCE_MODULE = "test_acceptance"
GRADE_TIMEOUT_S = 120

_RUNNER = """
import json, sys, unittest
out = {}
class Result(unittest.TestResult):
    def addSuccess(self, test): out.setdefault(test.id(), "pass")
    def addFailure(self, test, err): out[test.id()] = "fail"
    def addError(self, test, err): out[test.id()] = "error"
    def addSubTest(self, test, subtest, err):
        if err is not None: out[test.id()] = "fail"
    def addSkip(self, test, reason): out[test.id()] = "skip"
sys.path.insert(0, ".")
unittest.defaultTestLoader.discover("tests", top_level_dir=".").run(Result())
sys.stdout.write(json.dumps(out))
"""


@dataclass(frozen=True)
class AddedTest:
    file: str
    name: str
    outcome: str
    """`pass`, `fail`, `error`, or `skip` from the agent's own suite. `error` when it did not collect."""
    relevant: bool
    """True when the file imports the task's target module. Irrelevance does not fail the run."""


@dataclass(frozen=True)
class Grade:
    acceptance_passed: int
    acceptance_total: int
    original_passed: int
    original_total: int
    regressions: tuple[str, ...]
    """Pre-existing tests that pass on the snapshot and do not pass after the run."""
    protected_changed: tuple[str, ...]
    """Pre-existing test files whose bytes differ from the snapshot, or that were deleted."""
    preexisting_altered: tuple[str, ...]
    """Pre-existing test files whose test content was modified, deleted, skipped, or weakened."""
    added_tests: tuple[AddedTest, ...]

    @property
    def added_failing(self) -> tuple[AddedTest, ...]:
        return tuple(item for item in self.added_tests if item.outcome != "pass")

    @property
    def correct(self) -> bool:
        """Tree verdict: acceptance, no regressions, pre-existing tests intact, added tests passing."""
        return (
            self.acceptance_passed == self.acceptance_total
            and not self.regressions
            and not self.preexisting_altered
            and not self.added_failing
        )

    @property
    def test_pass_rate(self) -> float:
        return (self.acceptance_passed + self.original_passed) / (self.acceptance_total + self.original_total)


def old_rule_success(result: Grade) -> bool:
    """The pre-ADR-0073 rule: any byte change to a pre-existing test file fails the run.

    Decision match is not part of this rule. It exists so a study can show the D2 effect beside
    `run_correct`.
    """
    return (
        result.acceptance_passed == result.acceptance_total and not result.regressions and not result.protected_changed
    )


def run_correct(result: Grade, *, decision_matches_gold: bool | None) -> bool:
    """A run is correct when the tree is correct and, if the task has gold, the decision matches it."""
    if decision_matches_gold is False:
        return False
    return result.correct


def run_tests(tree: Path, task: Task, python: str) -> dict[str, str]:
    """Outcome per test id for `tree` graded against the pristine tests plus `task`'s acceptance tests."""
    with tempfile.TemporaryDirectory(prefix="jev-ab-grade-") as scratch:
        work = Path(scratch) / "tree"
        shutil.copytree(tree, work, ignore=shutil.ignore_patterns(".git", "__pycache__"))
        shutil.rmtree(work / "tests", ignore_errors=True)
        shutil.copytree(SNAPSHOT / "tests", work / "tests", ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copyfile(task.acceptance, work / "tests" / f"{ACCEPTANCE_MODULE}.py")
        try:
            done = subprocess.run(
                [python, "-I", "-c", _RUNNER],
                cwd=work,
                capture_output=True,
                text=True,
                timeout=GRADE_TIMEOUT_S,
                check=False,
            )
        except subprocess.TimeoutExpired:
            # A hung suite is an outcome of the agent's tree, not a harness exception: every
            # expected id then counts as not passed, so the run is a measured failure.
            return {"<grading>": "timeout"}
    try:
        outcomes: dict[str, str] = json.loads(done.stdout)
    except json.JSONDecodeError:
        return {}
    return outcomes


def expected_ids(task: Task, python: str) -> tuple[str, ...]:
    """Every test id of the reference solution; all of them must pass there."""
    with tempfile.TemporaryDirectory(prefix="jev-ab-ref-") as scratch:
        tree = Path(scratch)
        shutil.copytree(SNAPSHOT, tree, dirs_exist_ok=True, ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copytree(task.reference, tree, dirs_exist_ok=True)
        outcomes = run_tests(tree, task, python)
    failing = sorted(test_id for test_id, outcome in outcomes.items() if outcome != "pass")
    if not outcomes or failing:
        raise RuntimeError(f"reference solution for {task.id} does not pass: {failing or 'no tests ran'}")
    return tuple(sorted(outcomes))


def changed_protected(tree: Path) -> tuple[str, ...]:
    changed: list[str] = []
    for rel in protected_files():
        after = tree / rel
        if not after.is_file() or _digest(after) != _digest(SNAPSHOT / rel):
            changed.append(rel)
    return tuple(changed)


def grade(tree: Path, task: Task, python: str) -> Grade:
    expected = expected_ids(task, python)
    outcomes = run_tests(tree, task, python)
    acceptance = [test_id for test_id in expected if f".{ACCEPTANCE_MODULE}." in test_id]
    original = [test_id for test_id in expected if test_id not in acceptance]
    altered, added = _test_edits(tree, task, python)
    return Grade(
        acceptance_passed=sum(outcomes.get(test_id) == "pass" for test_id in acceptance),
        acceptance_total=len(acceptance),
        original_passed=sum(outcomes.get(test_id) == "pass" for test_id in original),
        original_total=len(original),
        regressions=tuple(test_id for test_id in original if outcomes.get(test_id) != "pass"),
        protected_changed=changed_protected(tree),
        preexisting_altered=altered,
        added_tests=added,
    )


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _test_edits(tree: Path, task: Task, python: str) -> tuple[tuple[str, ...], tuple[AddedTest, ...]]:
    altered: list[str] = []
    pending: list[tuple[str, str, bool]] = []
    for rel in protected_files():
        after = tree / rel
        if not after.is_file():
            altered.append(rel)
            continue
        changed, names = _compare_source((SNAPSHOT / rel).read_text(encoding="utf-8"), _read(after))
        if changed:
            altered.append(rel)
        source = _read(after)
        for name in names:
            pending.append((rel, name, _relevant(source, task.target_module)))
    tests_root = tree / "tests"
    if tests_root.is_dir():
        known = set(protected_files())
        for path in sorted(tests_root.rglob("*.py")):
            rel = path.relative_to(tree).as_posix()
            if rel in known:
                continue
            source = _read(path)
            try:
                names = [name for name in _functions(source) if name.split(".")[-1].startswith("test_")]
            except SyntaxError:
                pending.append((rel, "(collect)", False))
                continue
            relevant = _relevant(source, task.target_module)
            pending.extend((rel, name, relevant) for name in names)
    outcomes = agent_outcomes(tree, python) if pending else {}
    added = tuple(AddedTest(file, name, _outcome(file, name, outcomes), relevant) for file, name, relevant in pending)
    return tuple(altered), added


def _has_pytest(python: str) -> bool:
    done = subprocess.run([python, "-I", "-c", "import pytest"], capture_output=True, check=False)
    return done.returncode == 0


def _junit_outcomes(tree: Path, work: Path, python: str) -> dict[str, str] | None:
    """The agent's own suite under pytest with a JUnit report, so unittest classes, pytest-style
    functions, `*_test.py`, and subpackages are all collected the way the agent ran them."""
    report = work / "junit.xml"
    try:
        subprocess.run(
            [
                python,
                "-I",
                "-m",
                "pytest",
                "-q",
                "-p",
                "no:cacheprovider",
                "-o",
                "addopts=",
                "-c",
                "/dev/null",
                f"--junitxml={report}",
                "--rootdir",
                str(work),
                "tests",
            ],
            cwd=work,
            capture_output=True,
            text=True,
            timeout=GRADE_TIMEOUT_S,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return None
    if not report.is_file():
        return {}
    outcomes: dict[str, str] = {}
    # The report is pytest's own JUnit writer output for this run's tests, not agent-authored XML.
    root = ET.parse(report).getroot()  # noqa: S314 - bounded, local, tool-generated
    for case in root.iter("testcase"):
        name = case.get("name") or ""
        classname = (case.get("classname") or "").split(".")
        qual = f"{classname[-1]}.{name}" if classname else name
        kind = "pass"
        for tag, outcome in (("failure", "fail"), ("error", "error"), ("skipped", "skip")):
            if case.find(tag) is not None:
                kind = outcome
        # The report carries no file attribute on this pytest, so a qualname collision across
        # files merges to the worst outcome: conservative, and it fails the run when either fails.
        if outcomes.get(qual) == "pass":
            outcomes[qual] = kind
        else:
            outcomes.setdefault(qual, kind)
    return outcomes


def _unittest_outcomes(work: Path, python: str) -> dict[str, str] | None:
    """The unittest fallback for a grader interpreter without pytest: id-keyed outcomes."""
    try:
        done = subprocess.run(
            [python, "-I", "-c", _RUNNER],
            cwd=work,
            capture_output=True,
            text=True,
            timeout=GRADE_TIMEOUT_S,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return None
    try:
        return dict(cast(dict[str, str], json.loads(done.stdout)))
    except json.JSONDecodeError:
        return {}


def agent_outcomes(tree: Path, python: str) -> dict[str, str] | None:
    """`{file::qualname: outcome}` for the agent's own suite, or None when it did not finish.

    The suite runs the way the agent ran it: pytest with a JUnit report when the grader
    interpreter has pytest, else unittest discovery. None (a hang) is a recorded outcome, not an
    exception: the caller records every pending test as `timeout`, which fails the run.
    """
    with tempfile.TemporaryDirectory(prefix="jev-ab-added-") as scratch:
        work = Path(scratch) / "tree"
        shutil.copytree(tree, work, ignore=shutil.ignore_patterns(".git", "__pycache__"))
        if _has_pytest(python):
            return _junit_outcomes(tree, work, python)
        return _unittest_outcomes(work, python)


def _outcome(file: str, qualname: str, outcomes: Mapping[str, str] | None) -> str:
    del file  # the JUnit report on this pytest carries no file attribute; the qualname is the key
    if qualname == "(collect)":
        return "error"
    if outcomes is None:
        return "timeout"
    hit = outcomes.get(qualname)
    if hit is not None:
        return hit
    hits = [value for key, value in outcomes.items() if key.endswith("." + qualname)]
    if not hits:
        return "error"
    return next((item for item in hits if item != "pass"), "pass")


def _relevant(source: str, target_module: str) -> bool:
    """Deterministic: the file imports the task's target module. An unparsable file is not relevant."""
    if not target_module:
        return False
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return False
    for node in ast.walk(tree):
        if isinstance(node, ast.Import) and any(alias.name.split(".")[0] == target_module for alias in node.names):
            return True
        if isinstance(node, ast.ImportFrom) and node.module and node.module.split(".")[0] == target_module:
            return True
    return False


def _compare_source(old: str, new: str) -> tuple[bool, list[str]]:
    """Whether pre-existing test content changed, and the qualnames of added test functions."""
    try:
        old_fns, new_fns = _functions(old), _functions(new)
        old_classes, new_classes = _class_statements(old), _class_statements(new)
    except SyntaxError:
        return True, []
    altered = any(new_fns.get(name) != text for name, text in old_fns.items())
    altered = altered or not _subsequence(_imports(old), _imports(new))
    altered = altered or _fixed_statements(old) != _fixed_statements(new)
    altered = altered or any(name not in new_classes or new_classes[name] != text for name, text in old_classes.items())
    added = sorted(name for name in new_fns if name not in old_fns and name.split(".")[-1].startswith("test_"))
    return altered, added


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _start(node: ast.AST) -> int | None:
    lineno = getattr(node, "lineno", None)
    if not isinstance(lineno, int):
        return None
    decos = getattr(node, "decorator_list", [])
    deco_lines = [deco.lineno for deco in decos if isinstance(getattr(deco, "lineno", None), int)]
    return min([lineno, *deco_lines])


def _slice(lines: list[str], node: ast.AST) -> str:
    start = _start(node)
    end = getattr(node, "end_lineno", None)
    if not isinstance(start, int) or not isinstance(end, int):
        return ""
    return "\n".join(lines[start - 1 : end])


def _functions(source: str) -> dict[str, str]:
    lines = source.splitlines()
    found: dict[str, str] = {}

    def walk(body: list[ast.stmt], prefix: str) -> None:
        for node in body:
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                found[f"{prefix}{node.name}"] = _slice(lines, node)
            elif isinstance(node, ast.ClassDef):
                walk(node.body, f"{prefix}{node.name}.")

    walk(ast.parse(source).body, "")
    return found


def _imports(source: str) -> list[str]:
    lines = source.splitlines()
    return [_slice(lines, node) for node in ast.parse(source).body if isinstance(node, ast.Import | ast.ImportFrom)]


def _fixed_statements(source: str) -> list[str]:
    """Module-level statements that are not imports, functions, or classes. Inserting one is a change."""
    lines = source.splitlines()
    return [
        _slice(lines, node)
        for node in ast.parse(source).body
        if not isinstance(node, ast.Import | ast.ImportFrom | ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)
    ]


def _class_statements(source: str) -> dict[str, list[str]]:
    """Non-function statements of each class. A new test method is not one of these."""
    lines = source.splitlines()
    found: dict[str, list[str]] = {}
    for node in ast.parse(source).body:
        if isinstance(node, ast.ClassDef):
            found[node.name] = [
                _slice(lines, child)
                for child in node.body
                if not isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef)
            ]
    return found


def _subsequence(old: list[str], new: list[str]) -> bool:
    index = 0
    for item in new:
        if index < len(old) and item == old[index]:
            index += 1
    return index == len(old)
