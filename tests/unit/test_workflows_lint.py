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

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

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


_SHA = "a1" * 20


@pytest.mark.parametrize(
    ("step", "accepted"),
    [
        ("- uses: ./actions/local", True),
        ("- uses: ./../outside", False),
        (f"- uses: 'owner/repo/path@{_SHA}' # v2", True),
        (f'uses: "docker://registry:123/repo@sha256:{_SHA}{_SHA[:24]}"', True),
        ("- uses: owner/repo@v1", False),
        ("- uses: owner/repo@main", False),
        ("- uses: owner/repo@1234567", False),
        ("- uses: docker://image:latest", False),
        (f"- {{uses: owner/repo@{_SHA}}}", False),
        (f"- {{\n    uses: owner/repo@{_SHA}\n  }}", False),
        (f"- uses: &step owner/repo@{_SHA}", False),
        ("- uses: *step", False),
        ("- uses: |\n    owner/repo@main", False),
        ("- 'uses': owner/repo@main", False),
    ],
)
@pytest.mark.parametrize("filename", [".github/workflows/build.yaml", "tools/action.yml"])
def test_scout_034_action_reference_policy(tmp_path: Path, step: str, accepted: bool, filename: str) -> None:
    workflows = tmp_path / ".github/workflows"
    workflows.mkdir(parents=True)
    (workflows / "ci.yml").write_text("name: test\n")
    path = tmp_path / filename
    path.parent.mkdir(exist_ok=True)
    path.write_text("steps:\n  " + step + "\n")
    script = tmp_path / "scripts/check_workflows.py"
    script.parent.mkdir()
    shutil.copyfile(SCRIPT, script)
    result = run(script, stubbed(tmp_path, "exit 0\n"))
    assert result.returncode == (0 if accepted else 1), result.stderr
    if not accepted:
        assert f"{filename}:2:" in result.stderr


def test_scout_034_real_workflows_have_immutable_actions(tmp_path: Path) -> None:
    assert run(SCRIPT, stubbed(tmp_path, "exit 0\n")).returncode == 0


def test_scout_006_both_publish_paths_share_verified_input() -> None:
    release = (SCRIPT.parents[1] / ".github/workflows/release.yml").read_text()
    verify, publish = release.split("\n  pypi:\n")
    assert release.count("id-token: write") == release.count("uv publish ") == 1
    assert verify.index("uv pip install ") < verify.index("uses: actions/upload-artifact@")
    assert "needs.verify.result == 'success'" in publish
    assert "github.event_name == 'workflow_dispatch' && inputs.republish_tag != ''" in publish
    for restriction in (
        ".head_repository.full_name == $repo",
        '.head_branch == "main"',
        '.path == ".github/workflows/release.yml"',
    ):
        assert restriction in publish
    assert publish.index('gh run download "$SOURCE_RUN" --name "$receipt"') < publish.index(
        'gh release download "$TAG"'
    )
    assert publish.index('cmp "$RUNNER_TEMP/receipt/SHA256SUMS.txt"') < publish.index("uv publish ")
