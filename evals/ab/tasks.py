"""The fixed outcome tasks and the throwaway repository each run works in.

Every task hinges on one judgment a Jev tool is for (a boundary, a classification, a choice among
plausible patches). `task.json` holds the judgment's options, the gold decision and where the snapshot
settles it, the Jev tool the with-Jev arm is told to use, and the signatures of the wrong options. It
stays in the harness: the agent under test only ever sees a fresh copy of `fixture/snapshot` (plus the
task's overlay) in a temporary directory outside this repo, so it cannot read the gold or the grader.
"""

import hashlib
import json
import os
import shutil
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

FIXTURE = Path(__file__).parent / "fixture"
SNAPSHOT = FIXTURE / "snapshot"
TASK_IDS = ("j1-refund-window", "j2-ticket-route", "j3-installment-patch")
TEST_COMMAND = "python3 -m unittest discover -s tests"


@dataclass(frozen=True)
class Judgment:
    kind: str
    """What the task hinges on: `boundary`, `classification`, or `patch choice`."""
    jev_tool: str
    """The Jev tool the with-Jev arm is told to use for this judgment."""
    question: str
    options: Mapping[str, str]
    """Option id to description; the agent names one id as its decision."""
    gold: str | None
    """The reference decision, or None when no gold exists (then judge accuracy is not reported)."""
    wrong_branch_signatures: Mapping[str, tuple[str, ...]]
    """Regexes that mark a write or command committing to a non-gold option. Absent for a task whose
    wrong options leave no distinctive trace; such a task reports no wrong-branch count."""


@dataclass(frozen=True)
class Task:
    id: str
    prompt: str
    acceptance: Path
    """Hidden acceptance tests, added only at grading time."""
    overlay: Path | None
    """Files copied over the snapshot before the run (untrusted input the agent must read)."""
    reference: Path
    """A known-good solution; grading it defines the expected test ids."""
    distractors: Mapping[str, Path]
    """Option id to a solution that implements that wrong option; each must fail acceptance."""
    judgment: Judgment


def load_task(task_id: str) -> Task:
    root = FIXTURE / "tasks" / task_id
    overlay = root / "overlay"
    spec = json.loads((root / "task.json").read_text(encoding="utf-8"))
    judgment = Judgment(
        kind=spec["judgment"],
        jev_tool=spec["jev_tool"],
        question=spec["question"],
        options=dict(spec["options"]),
        gold=spec.get("gold"),
        wrong_branch_signatures={k: tuple(v) for k, v in spec.get("wrong_branch_signatures", {}).items()},
    )
    if judgment.gold is not None and judgment.gold not in judgment.options:
        raise ValueError(f"{task_id}: gold {judgment.gold!r} is not an option")
    if unknown := set(judgment.wrong_branch_signatures) - set(judgment.options):
        raise ValueError(f"{task_id}: signatures for unknown options {sorted(unknown)}")
    distractors = root / "distractors"
    return Task(
        id=task_id,
        prompt=(root / "prompt.md").read_text(encoding="utf-8").strip(),
        acceptance=root / "acceptance_test.py",
        overlay=overlay if overlay.is_dir() else None,
        reference=root / "reference",
        distractors={p.name: p for p in sorted(distractors.iterdir())} if distractors.is_dir() else {},
        judgment=judgment,
    )


def load_tasks() -> tuple[Task, ...]:
    return tuple(load_task(task_id) for task_id in TASK_IDS)


def protected_files() -> tuple[str, ...]:
    """Snapshot-relative paths of the pre-existing tests. Changing or deleting one is never allowed."""
    return tuple(sorted(p.relative_to(SNAPSHOT).as_posix() for p in (SNAPSHOT / "tests").glob("*.py")))


def fixture_digest() -> str:
    """sha256 over every fixture file's path and bytes: the task repository's revision."""
    digest = hashlib.sha256()
    for path in sorted(p for p in FIXTURE.rglob("*") if p.is_file() and "__pycache__" not in p.parts):
        digest.update(path.relative_to(FIXTURE).as_posix().encode() + b"\0" + path.read_bytes() + b"\0")
    return digest.hexdigest()


def materialize(task: Task, dest: Path) -> None:
    """Fill the empty directory `dest` with the snapshot, the task overlay, and a one-commit git repo."""
    shutil.copytree(SNAPSHOT, dest, dirs_exist_ok=True, ignore=shutil.ignore_patterns("__pycache__"))
    if task.overlay is not None:
        shutil.copytree(task.overlay, dest, dirs_exist_ok=True)
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": str(dest),
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_SYSTEM": os.devnull,
        "GIT_AUTHOR_NAME": "shopkit",
        "GIT_AUTHOR_EMAIL": "shopkit@example.invalid",
        "GIT_COMMITTER_NAME": "shopkit",
        "GIT_COMMITTER_EMAIL": "shopkit@example.invalid",
    }
    for args in (["init", "-q", "-b", "main"], ["add", "-A"], ["commit", "-q", "--no-gpg-sign", "-m", "snapshot"]):
        subprocess.run(["git", *args], cwd=dest, env=env, check=True)
