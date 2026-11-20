"""scripts/check_pr_title.py: the pr-title workflow's check, run as the workflow runs it."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "check_pr_title.py"


def run(title: str | None) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if k != "PR_TITLE"}
    if title is not None:
        env["PR_TITLE"] = title
    return subprocess.run([sys.executable, str(SCRIPT)], capture_output=True, text=True, env=env, check=False)


@pytest.mark.parametrize(
    "title",
    [
        "fix: report oversized git ranges as input budget errors",
        "feat(gate): say who settles each held row",
        "feat!: refuse colliding caller IDs",
        "chore(main): release 0.12.0",
        "deps: update the locked runtime",
        "ci: check pull request titles",
        "docs: backfill the 0.11.0 changelog",
    ],
)
def test_conventional_titles_pass(title: str) -> None:
    proc = run(title)
    assert proc.returncode == 0, proc.stderr


@pytest.mark.parametrize(
    "title",
    [
        "Fix release PR smoke and bump the minor version on every release",
        "Merge pull request #34 from PyModel/fix/old-python-guard-release",
        "Fix: capitalised type",
        "fixes: unknown type",
        "fix:missing space",
        "fix: ",
        "fix(): empty scope",
        "fix: two lines\nfeat: smuggled entry",
        "$(touch pwned): not a type",
        "",
    ],
)
def test_other_titles_fail_and_name_the_format(title: str) -> None:
    proc = run(title)
    assert proc.returncode == 1
    assert "<type>[(scope)][!]: <subject>" in proc.stderr


def test_a_missing_title_fails() -> None:
    assert run(None).returncode == 1


def test_the_workflow_rechecks_edited_titles_through_the_environment() -> None:
    workflow = (ROOT / ".github/workflows/pr-title.yml").read_text()
    assert "types: [opened, edited, reopened, synchronize]" in workflow
    assert "run: uv run --no-project python scripts/check_pr_title.py" in workflow
    # Every expression that reads the event, anywhere in the file, including multi-line run blocks.
    reads = [line.strip() for line in workflow.splitlines() if "github.event" in line]
    assert reads == [
        "group: pr-title-${{ github.event.pull_request.number }}",
        "PR_TITLE: ${{ github.event.pull_request.title }}",
    ], "the title must reach the check only through the environment"
