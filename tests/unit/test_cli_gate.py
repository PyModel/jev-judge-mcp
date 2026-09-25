"""The harness-agnostic CLI refuses paths it must not read, and does not print allow."""

import json
from pathlib import Path

import pytest

from jev_judge_mcp.cli import completion_hook_main, gate_main, judge_main


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
    """The hook does not match Bash generally. A matching command with no local files fails open."""
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
