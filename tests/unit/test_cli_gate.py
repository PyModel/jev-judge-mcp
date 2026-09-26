"""The harness-agnostic CLI refuses paths it must not read, and does not print allow."""

import json
import subprocess
from pathlib import Path

import pytest

from jev_judge_mcp.cli import completion_hook_main, completion_matches, gate_main, judge_main

_PUSH = json.dumps({"tool_name": "Bash", "tool_input": {"command": "git push origin main"}})
_CREDENTIALS = (
    "JEV_PROVIDER",
    "TYPESAFE_API_KEY",
    "TYPESAFE_BASE_URL",
    "OPENROUTER_API_KEY",
    "JEV_CLOUDFLARE_API_TOKEN",
    "CLOUDFLARE_API_TOKEN",
    "CLOUDFLARE_ACCOUNT_ID",
    "AI_GATEWAY_API_KEY",
    "JEV_API_KEY",
    "JEV_API_BASE_URL",
)


def _ask(stdout: str) -> str:
    decision = json.loads(stdout)
    return str(decision["hookSpecificOutput"]["permissionDecision"])


def _clear_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in _CREDENTIALS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("JEV_MCP_KEY_FILE", "/nonexistent/jev-mcp-key")
    monkeypatch.delenv("GIT_DIR", raising=False)
    monkeypatch.delenv("GIT_WORK_TREE", raising=False)


def _repo_with_diff(tmp_path: Path) -> Path:
    """Two commits so ``git diff HEAD~1`` is a real patch, not an empty range."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True, capture_output=True)
    (repo / "x.py").write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "x.py"], check=True, capture_output=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "-c",
            "user.email=gate@example.invalid",
            "-c",
            "user.name=gate test",
            "commit",
            "-qm",
            "init",
        ],
        check=True,
        capture_output=True,
    )
    (repo / "x.py").write_text("x = 2\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "x.py"], check=True, capture_output=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "-c",
            "user.email=gate@example.invalid",
            "-c",
            "user.name=gate test",
            "commit",
            "-qm",
            "set x to 2",
        ],
        check=True,
        capture_output=True,
    )
    (repo / "claims.json").write_text(
        json.dumps({"request": "Set x to 2", "claims": ["The patch sets x to 2."]}),
        encoding="utf-8",
    )
    (repo / "tests.log").write_text("1 passed\n", encoding="utf-8")
    return repo


def test_gate_refuses_an_undecodable_tests_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(Path.cwd())
    claims = Path("README.md")
    tests = tmp_path / "tests.log"
    # A path outside the repo is already refused. An inside-repo file that is not UTF-8 must
    # still return an envelope, not a traceback.
    inside = Path("tests.log.bad")
    inside.write_bytes(b"\xff\xfe not utf-8")
    try:
        code = gate_main(["--diff", "HEAD", "--claims", str(claims), "--tests", str(inside)])
    finally:
        inside.unlink(missing_ok=True)
    captured = capsys.readouterr()
    assert code == 2
    assert json.loads(captured.out)["error"]["code"] == "invalid_arguments"
    del tests


def test_judge_refuses_a_secret_too_short_to_redact(monkeypatch: pytest.MonkeyPatch) -> None:
    import sys

    from jev_judge_mcp.server import main

    monkeypatch.setenv("TYPESAFE_API_KEY", "x" * 2)
    monkeypatch.setattr(sys, "argv", ["jev-judge-mcp", "judge", "jev_verify"])
    with pytest.raises(SystemExit) as caught:
        main()
    assert "shorter than" in str(caught.value)


def test_judge_usage_is_exit_2(capsys: pytest.CaptureFixture[str]) -> None:
    assert judge_main([]) == 2
    assert capsys.readouterr().out == ""


def test_gate_refuses_a_path_outside_the_repo(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    outside = tmp_path / "claims.txt"
    outside.write_text("a claim\n", encoding="utf-8")
    code = gate_main(["--diff", "HEAD", "--claims", str(outside), "--tests", "README.md"])
    captured = capsys.readouterr()
    assert code == 2
    envelope = json.loads(captured.out)
    assert envelope["error"]["code"] == "invalid_arguments"
    assert envelope["action"] is None


def test_completion_hook_abstains_unless_the_command_is_push_or_pr(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A non-matching Bash command gets no decision. git push reaches the gate path."""
    idle = completion_hook_main([], text=json.dumps({"tool_name": "Bash", "tool_input": {"command": "git status"}}))
    idle_out = capsys.readouterr()
    assert idle == 0
    assert idle_out.out == ""
    matched = completion_hook_main(
        [],
        text=json.dumps({"tool_name": "Bash", "tool_input": {"command": "git push origin main"}}),
        environ={},
    )
    matched_out = capsys.readouterr()
    assert matched == 0
    assert matched_out.out == ""
    assert "error.code=invalid_arguments" in matched_out.err
    assert "allow" not in matched_out.out


def test_required_completion_hook_asks_on_a_keyless_push(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The dogfood reproduction: key unset, flag set, a git push event, stdout is ask."""
    repo = _repo_with_diff(tmp_path)
    _clear_credentials(monkeypatch)
    monkeypatch.chdir(repo)
    code = completion_hook_main(
        [],
        text=_PUSH,
        environ={
            "JEV_HOOK_REQUIRED": "1",
            "JEV_COMPLETION_DIFF": "HEAD~1",
            "JEV_COMPLETION_CLAIMS": "claims.json",
            "JEV_COMPLETION_TESTS": "tests.log",
        },
    )
    captured = capsys.readouterr()
    assert code == 0
    assert _ask(captured.out) == "ask"
    assert "auth" in captured.out
    assert "allow" not in captured.out


@pytest.mark.parametrize("body", ["not-json", "[]"])
def test_required_completion_hook_asks_on_bad_stdin(body: str, capsys: pytest.CaptureFixture[str]) -> None:
    code = completion_hook_main([], text=body, environ={"JEV_HOOK_REQUIRED": "1"})
    captured = capsys.readouterr()
    assert code == 0
    assert _ask(captured.out) == "ask"
    assert "allow" not in captured.out


def test_required_completion_hook_asks_when_gate_never_calls_the_provider(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A path outside the repo fails inside gate, before any provider call."""
    monkeypatch.chdir(Path.cwd())
    code = completion_hook_main(
        [],
        text=_PUSH,
        environ={
            "JEV_HOOK_REQUIRED": "1",
            "JEV_COMPLETION_DIFF": "HEAD",
            "JEV_COMPLETION_CLAIMS": "/etc/hosts",
            "JEV_COMPLETION_TESTS": "README.md",
        },
    )
    captured = capsys.readouterr()
    assert code == 0
    assert _ask(captured.out) == "ask"
    assert "invalid_arguments" in captured.out
    assert "allow" not in captured.out


def test_required_flag_stays_silent_for_a_non_matching_command(capsys: pytest.CaptureFixture[str]) -> None:
    code = completion_hook_main(
        [],
        text=json.dumps({"tool_name": "Bash", "tool_input": {"command": "echo dogfood-unrelated"}}),
        environ={"JEV_HOOK_REQUIRED": "1"},
    )
    captured = capsys.readouterr()
    assert code == 0
    assert captured.out == ""


def test_completion_hook_stays_open_without_the_flag(capsys: pytest.CaptureFixture[str]) -> None:
    code = completion_hook_main([], text="not-json", environ={})
    captured = capsys.readouterr()
    assert code == 0
    assert captured.out == ""
    assert "error.code=invalid_arguments" in captured.err


def test_required_flag_stays_open_after_the_provider_was_reached(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A timeout means the provider was called. The flag does not turn that into allow, or into ask."""

    def gate(_argv: object) -> int:
        import sys

        sys.stdout.write(json.dumps({"error": {"code": "timeout", "message": "timed out"}}) + "\n")
        return 1

    monkeypatch.setattr("jev_judge_mcp.cli.gate_main", gate)
    code = completion_hook_main(
        [],
        text=_PUSH,
        environ={
            "JEV_HOOK_REQUIRED": "1",
            "JEV_COMPLETION_DIFF": "HEAD",
            "JEV_COMPLETION_CLAIMS": "claims.json",
            "JEV_COMPLETION_TESTS": "tests.log",
        },
    )
    captured = capsys.readouterr()
    assert code == 0
    assert captured.out == ""
    assert "error.code=timeout" in captured.err
    assert "allow" not in captured.out


@pytest.mark.parametrize(
    ("command", "matches"),
    [
        pytest.param("git  push", True, id="extra-space"),
        pytest.param("git\tpush", True, id="tab"),
        pytest.param("git push origin main", True, id="push-with-args"),
        pytest.param("gh pr create", True, id="pr-create"),
        pytest.param("gh pr merge --squash", True, id="pr-merge-with-flag"),
        pytest.param("git pushback", False, id="longer-word"),
        pytest.param("git pushd", False, id="pushd"),
        pytest.param("gh pr checkout", False, id="other-pr-verb"),
        pytest.param("GIT PUSH", False, id="case-sensitive"),
        pytest.param("git push -m \"unterminated", False, id="untokenizable-abstains"),
    ],
)
def test_completion_matching_reads_tokens_not_a_string_prefix(command: str, matches: bool) -> None:
    """The command is tokenized the way the shell reads it: `git  push` gates, `git pushback` does not.

    A command that does not tokenize (`git push -m \"unterminated`) abstains: the shell cannot
    run it either, so there is nothing to gate.
    """
    assert completion_matches(command) is matches
