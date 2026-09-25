"""The harness-agnostic CLI refuses paths it must not read, and does not print allow."""

import json
from pathlib import Path

import pytest

from jev_judge_mcp.cli import completion_matches, gate_main, judge_main


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


def test_completion_hook_does_not_match_bash_generally() -> None:
    assert completion_matches("git push origin main")
    assert completion_matches("gh pr create --fill")
    assert completion_matches("gh pr merge 1")
    assert not completion_matches("git status")
    assert not completion_matches("Bash")
    assert not completion_matches("rm -rf /")


def test_completion_hook_abstains_on_other_commands(capsys: pytest.CaptureFixture[str]) -> None:
    from jev_judge_mcp.cli import completion_hook_main

    code = completion_hook_main([], text=json.dumps({"tool_name": "Bash", "tool_input": {"command": "git status"}}))
    captured = capsys.readouterr()
    assert code == 0
    assert captured.out == ""
