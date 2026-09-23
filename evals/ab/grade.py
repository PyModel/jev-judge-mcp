"""Deterministic grading: completion, regressions, and test pass rate all come from one unittest run.

The agent's tree is copied, its `tests/` is replaced by the pristine snapshot tests plus the hidden
acceptance tests, and every test id is recorded. The expected ids come from grading the task's
reference solution, so a module that fails to import counts all of its tests as failed.
"""

import hashlib
import json
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

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
class Grade:
    acceptance_passed: int
    acceptance_total: int
    original_passed: int
    original_total: int
    regressions: tuple[str, ...]
    """Pre-existing tests that pass on the snapshot and do not pass after the run."""
    protected_changed: tuple[str, ...]
    """Pre-existing test files the agent modified or deleted."""

    @property
    def correct(self) -> bool:
        return self.acceptance_passed == self.acceptance_total and not self.regressions and not self.protected_changed

    @property
    def test_pass_rate(self) -> float:
        return (self.acceptance_passed + self.original_passed) / (self.acceptance_total + self.original_total)


def run_tests(tree: Path, task: Task, python: str) -> dict[str, str]:
    """Outcome per test id for `tree` graded against the pristine tests plus `task`'s acceptance tests."""
    with tempfile.TemporaryDirectory(prefix="jev-ab-grade-") as scratch:
        work = Path(scratch) / "tree"
        shutil.copytree(tree, work, ignore=shutil.ignore_patterns(".git", "__pycache__"))
        shutil.rmtree(work / "tests", ignore_errors=True)
        shutil.copytree(SNAPSHOT / "tests", work / "tests", ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copyfile(task.acceptance, work / "tests" / f"{ACCEPTANCE_MODULE}.py")
        done = subprocess.run(
            [python, "-I", "-c", _RUNNER],
            cwd=work,
            capture_output=True,
            text=True,
            timeout=GRADE_TIMEOUT_S,
            check=False,
        )
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
    return Grade(
        acceptance_passed=sum(outcomes.get(test_id) == "pass" for test_id in acceptance),
        acceptance_total=len(acceptance),
        original_passed=sum(outcomes.get(test_id) == "pass" for test_id in original),
        original_total=len(original),
        regressions=tuple(test_id for test_id in original if outcomes.get(test_id) != "pass"),
        protected_changed=changed_protected(tree),
    )


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
