"""The workflow lint guard: actionlint findings fail `make lint`; both linters are required.

The check guards the push path (ADR-0056): the gate's Linux image ships actionlint, so every
push runs it for real. These tests hold the script's own contract at its real boundary —
subprocess and PATH — with a stub actionlint that decides what it reports:

- findings (exit 1) fail the check and the finding text is passed through;
- a clean run passes;
- a missing linter or a timeout fails closed;
- a crashed actionlint (any other exit) is a tool failure, not a pass;
- a copy of the script with no .github/workflows above it fails rather than passing vacuously.
"""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check_workflows.py"


def stubbed(tmp_path: Path, body: str, *, version: str = "1.7.13") -> dict[str, str]:
    stub_dir = tmp_path / "bin"
    stub_dir.mkdir(exist_ok=True)
    stub = stub_dir / "actionlint"
    stub.write_text(f'#!/bin/bash\nif [[ "$1" == -version ]]; then echo {version}; exit 0; fi\n' + body)
    stub.chmod(0o755)
    shellcheck = stub_dir / "shellcheck"
    shellcheck.write_text("#!/bin/bash\nexit 0\n")
    shellcheck.chmod(0o755)
    return {"PATH": str(stub_dir), "HOME": str(tmp_path)}


def run(script: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(script)],
        cwd=env["HOME"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )


def test_findings_fail_the_check(tmp_path: Path) -> None:
    env = stubbed(tmp_path, 'printf "%s\\n" ".github/workflows/ci.yml:1:9: boom [SC9999]" >&2\nexit 1\n')
    proc = run(SCRIPT, env)
    assert proc.returncode == 1, proc.stderr
    assert "workflow lint failed" in proc.stderr
    assert "ci.yml:1:9: boom" in proc.stderr


def test_a_clean_actionlint_passes(tmp_path: Path) -> None:
    # More than a pipe buffer on both streams must drain before the linter exits.
    proc = run(SCRIPT, stubbed(tmp_path, "/usr/bin/head -c 262144 /dev/zero\n/usr/bin/head -c 262144 /dev/zero >&2\n"))
    assert proc.returncode == 0, proc.stderr
    assert "workflow lint passed" in proc.stdout


def test_an_absent_binary_fails_the_check(tmp_path: Path) -> None:
    empty = tmp_path / "empty-bin"
    empty.mkdir()
    proc = run(SCRIPT, {"PATH": str(empty), "HOME": str(tmp_path)})
    assert proc.returncode == 2, proc.stderr
    assert "actionlint" in proc.stderr


def test_a_crashed_actionlint_is_a_tool_failure(tmp_path: Path) -> None:
    proc = run(SCRIPT, stubbed(tmp_path, 'printf "%s\\n" "internal" >&2\nexit 3\n'))
    assert proc.returncode == 2, proc.stderr
    assert "could not run actionlint" in proc.stderr


@pytest.mark.parametrize("empty_directory", [False, True])
def test_a_repo_without_workflows_is_an_error_not_a_pass(tmp_path: Path, empty_directory: bool) -> None:
    if empty_directory:
        (tmp_path / ".github/workflows").mkdir(parents=True)
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
    before_pypi, pypi = release.split("\n  pypi:\n")
    build, post_build = before_pypi.split("\n  verify:\n")
    verify, post_verify = post_build.split("\n  publish:\n")
    publish, prepare = post_verify.split("\n  prepare_pypi:\n")
    assert release.count("id-token: write") == release.count("uv publish ") == 1
    assert ": write" not in build.split("\n  build:\n")[1]
    assert ": write" not in verify
    assert "uv pip install " in verify
    assert "uses: actions/upload-artifact@" not in verify
    assert publish.index("Save the verified manifest") < publish.index("- name: Publish the release")
    assert "needs: [version, build, verify]" in publish
    assert "needs.publish.result == 'success'" in prepare
    assert "github.event_name == 'workflow_dispatch' && inputs.republish_tag != ''" in prepare
    for restriction in (
        ".head_repository.full_name == $repo",
        '.head_branch == "main"',
        '.path == ".github/workflows/release.yml"',
    ):
        assert restriction in prepare
    assert prepare.index('gh run download "$SOURCE_RUN" --name "$receipt"') < prepare.index(
        'gh release download "$TAG"'
    )
    assert prepare.index('cmp "$RUNNER_TEMP/receipt/SHA256SUMS.txt"') < prepare.index("uses: actions/upload-artifact@")
    assert "artifact-ids: ${{ needs.prepare_pypi.outputs.artifact_id }}" in pypi
    assert pypi.index('test "$digest" = "$VERIFIED_MANIFEST"') < pypi.index("uv publish ")
    assert "checkout@" not in pypi


def test_a_missing_shellcheck_fails_the_check(tmp_path: Path) -> None:
    env = stubbed(tmp_path, "exit 0\n")
    (tmp_path / "bin" / "shellcheck").unlink()
    proc = run(SCRIPT, env)
    assert proc.returncode == 2
    assert "shellcheck" in proc.stderr


def lint_with_deadline(script: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            "-c",
            "import runpy,sys; sys.platform='darwin'; sys.exit(runpy.run_path(sys.argv[1])['main'](timeout=0.5))",
            str(script),
        ],
        cwd=env["HOME"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=6,
    )


def test_actionlint_timeout_names_the_stalled_workflow_and_fails(tmp_path: Path) -> None:
    script = tmp_path / "scripts/check_workflows.py"
    script.parent.mkdir()
    shutil.copyfile(SCRIPT, script)
    workflows = tmp_path / ".github/workflows"
    workflows.mkdir(parents=True)
    for name in ("build.yml", "release.yaml"):
        (workflows / name).write_text("name: test\n")
    env = stubbed(
        tmp_path,
        'for arg in "$@"; do [[ "$arg" == */release.yaml ]] && exec /bin/sleep 10; done\nexit 0\n',
    )
    go = tmp_path / "bin/go"
    go.write_text('#!/bin/bash\n/bin/cp /usr/bin/true "$GOBIN/actionlint"\n')
    go.chmod(0o755)
    proc = lint_with_deadline(script, env)
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert ".github/workflows/release.yaml" in proc.stderr and "timed out" in proc.stderr
    assert "build.yml" not in proc.stderr


@pytest.mark.parametrize("go_available", [False, True])
def test_macos_pipe_fix_never_skips_shellcheck(tmp_path: Path, go_available: bool) -> None:
    env = stubbed(tmp_path, "exec /bin/sleep 10\n", version="1.7.12")
    if go_available:
        go = tmp_path / "bin/go"
        go.write_text(
            "#!/bin/bash\n"
            '[[ "$1" == install && "$2" == github.com/rhysd/actionlint/cmd/actionlint@*fd33e9f582a0 ]] || exit 2\n'
            '/bin/cp "$HOME/fixed" "$GOBIN/actionlint"\n'
        )
        fixed = tmp_path / "fixed"
        fixed.write_text('#!/bin/bash\nexec shellcheck "$@"\n')
        fixed.chmod(0o755)
        go.chmod(0o755)
        shellcheck = tmp_path / "bin/shellcheck"
        shellcheck.write_text('#!/bin/bash\nprintf "SC2086 from fixed linter\\n" >&2\nexit 1\n')
    proc = lint_with_deadline(SCRIPT, env)
    assert proc.returncode == (1 if go_available else 2)
    assert "timed out" not in proc.stderr
    if go_available:
        assert "SC2086 from fixed linter" in proc.stderr
