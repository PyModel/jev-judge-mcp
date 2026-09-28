"""The workflow lint guard: actionlint findings fail `make lint`; a missing binary skips loudly.

The check guards the push path (ADR-0056): the gate's Linux image ships actionlint, so every
push runs it for real. These tests hold the script's own contract at its real boundary —
subprocess and PATH — with a stub actionlint that decides what it reports:

- findings (exit 1) fail the check and the finding text is passed through;
- a clean run passes;
- an absent binary skips with one stderr line, never a silent pass;
- a crashed actionlint (any other exit) is a tool failure, not a pass;
- a copy of the script with no .github/workflows above it fails rather than passing vacuously.
"""

import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check_workflows.py"


def stubbed(tmp_path: Path, body: str) -> dict[str, str]:
    stub_dir = tmp_path / "bin"
    stub_dir.mkdir(exist_ok=True)
    stub = stub_dir / "actionlint"
    stub.write_text("#!/bin/bash\n" + body, encoding="utf-8")
    stub.chmod(0o755)
    return {"PATH": str(stub_dir), "HOME": str(tmp_path)}


def run(script: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(script)],
        cwd=env["HOME"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_findings_fail_the_check(tmp_path: Path) -> None:
    env = stubbed(tmp_path, 'printf "%s\\n" ".github/workflows/ci.yml:1:9: boom [SC9999]" >&2\nexit 1\n')
    proc = run(SCRIPT, env)
    assert proc.returncode == 1, proc.stderr
    assert "workflow lint failed" in proc.stderr
    assert "ci.yml:1:9: boom" in proc.stderr


def test_a_clean_actionlint_passes(tmp_path: Path) -> None:
    proc = run(SCRIPT, stubbed(tmp_path, "exit 0\n"))
    assert proc.returncode == 0, proc.stderr
    assert "workflow lint passed" in proc.stdout


def test_an_absent_binary_skips_with_one_stderr_line(tmp_path: Path) -> None:
    empty = tmp_path / "empty-bin"
    empty.mkdir()
    proc = run(SCRIPT, {"PATH": str(empty), "HOME": str(tmp_path)})
    assert proc.returncode == 0, proc.stderr
    assert "workflow lint skipped: actionlint is not installed" in proc.stderr


def test_a_crashed_actionlint_is_a_tool_failure(tmp_path: Path) -> None:
    proc = run(SCRIPT, stubbed(tmp_path, 'printf "%s\\n" "internal" >&2\nexit 3\n'))
    assert proc.returncode == 2, proc.stderr
    assert "could not run actionlint" in proc.stderr


def test_a_repo_without_workflows_is_an_error_not_a_pass(tmp_path: Path) -> None:
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    script_copy = elsewhere / "check_workflows.py"
    script_copy.write_text(SCRIPT.read_text(encoding="utf-8"), encoding="utf-8")
    proc = run(script_copy, stubbed(tmp_path, "exit 0\n"))
    assert proc.returncode == 2, proc.stderr
    assert ".github/workflows" in proc.stderr
