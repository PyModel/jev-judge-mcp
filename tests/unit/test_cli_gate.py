"""The harness-agnostic CLI refuses paths it must not read, and does not print allow."""

import json
import re
import subprocess
from pathlib import Path

import pytest

from jev_judge_mcp import cli
from jev_judge_mcp.cli import completion_hook_main, completion_matches, gate_main, judge_main
from tests.support.jev import FakeProvider

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


_REASON_SHAPE = re.compile(r"^Jev completion hook: gate action is (escalate|review|not auto)\.$")


def test_the_asks_reason_states_the_gate_action_as_a_fact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The regression: 'escalate. Read action, not verdict.' — an imperative inside a
    permissionDecisionReason — was read by a host model as a possible prompt injection. A reason
    states the gate's action as a fact and nothing else; the decision is the permissionDecision
    field, so a directive in the reason has nowhere legitimate to live."""
    repo = _repo_with_diff(tmp_path)
    monkeypatch.chdir(repo)
    code = completion_hook_main(
        [],
        text=_PUSH,
        environ={
            "JEV_COMPLETION_DIFF": "HEAD~1",
            "JEV_COMPLETION_CLAIMS": "claims.json",
            "JEV_COMPLETION_TESTS": "tests.log",
        },
        provider=FakeProvider({}),
    )
    captured = capsys.readouterr()
    assert code == 0
    assert _ask(captured.out) == "ask"
    reason = json.loads(captured.out)["hookSpecificOutput"]["permissionDecisionReason"]
    assert reason == "Jev completion hook: gate action is escalate."
    assert _REASON_SHAPE.match(reason), reason
    assert "Read action" not in reason


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
        pytest.param('git push -m "unterminated', False, id="untokenizable-abstains"),
        pytest.param("cd repo && git push", True, id="after-and"),
        pytest.param("make test; git push origin HEAD", True, id="after-semicolon"),
        pytest.param("git -C repo push", True, id="git-dash-C"),
        pytest.param("git -c core.hooksPath=x --no-pager push", True, id="git-globals"),
        pytest.param("echo 'git push'", False, id="quoted-is-an-argument"),
        pytest.param("git status | grep push", False, id="pipe-into-grep"),
    ],
)
def test_completion_matching_reads_tokens_not_a_string_prefix(command: str, matches: bool) -> None:
    """The command is tokenized the way the shell reads it: `git  push` gates, `git pushback` does not.

    A command that does not tokenize (`git push -m \"unterminated`) abstains: the shell cannot
    run it either, so there is nothing to gate.
    """
    assert completion_matches(command) is matches


def test_cli_gate_does_not_mark_a_file_it_read_self_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A tests log the CLI read is not self-reported (ADR-0067).

    The digest is not on the wire. The observable is `tests_weight` absent, and the provider
    state carrying the file text, so an unread file cannot pass.
    """
    repo = _repo_with_diff(tmp_path)
    monkeypatch.chdir(repo)
    raw = (repo / "tests.log").read_bytes()
    provider = FakeProvider({})
    code = gate_main(
        ["--diff", "HEAD~1", "--claims", "claims.json", "--tests", "tests.log"],
        provider=provider,
    )
    captured = capsys.readouterr()
    assert code == 0
    review = json.loads(captured.out)["payload"]["review"]
    assert "tests_weight" not in review
    assert provider.requests
    state = provider.requests[0][0]
    assert isinstance(state, dict)
    assert state.get("tests") == raw.decode("utf-8")


def test_a_path_containing_space_b_slash_is_split_at_the_header_midpoint() -> None:
    """`a/dir b/c b/dir b/c` has two ` b/` markers; the last one names `c`, the midpoint names the
    file. A rename falls back to the `+++ b/` line (git ends it with a tab when the path has spaces)."""
    same = "diff --git a/dir b/c b/dir b/c\nindex 1..2 100644\n--- a/dir b/c\n+++ b/dir b/c\t\n@@ -1 +1 @@\n-x\n+y\n"
    files = cli._split_unified(same)  # pyright: ignore[reportPrivateUsage]
    assert files is not None and [item["path"] for item in files] == ["dir b/c"]
    renamed = (
        "diff --git a/old b/new\nsimilarity index 90%\nrename from old\nrename to new\n"
        "--- a/old\n+++ b/new\n@@ -1 +1 @@\n-x\n+y\n"
    )
    files = cli._split_unified(renamed)  # pyright: ignore[reportPrivateUsage]
    assert files is not None and [item["path"] for item in files] == ["new"]


def test_the_completion_range_is_what_the_push_sends(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Without `JEV_COMPLETION_DIFF`: the commits ahead of the upstream, else the last commit; an
    empty range means nothing is being pushed and the hook abstains (ADR-0064 amendment)."""
    repo = _repo_with_diff(tmp_path)
    monkeypatch.chdir(repo)
    assert cli.completion_range({"JEV_COMPLETION_DIFF": "HEAD~1"}) == "HEAD~1"
    assert cli.completion_range({}) == "HEAD~1..HEAD"  # no upstream yet: the last commit
    branch = subprocess.run(
        ["git", "branch", "--show-current"], check=True, capture_output=True, text=True
    ).stdout.strip()
    subprocess.run(["git", "branch", "base", "HEAD~1"], check=True, capture_output=True)
    subprocess.run(["git", "branch", "--set-upstream-to=base", branch], check=True, capture_output=True)
    assert cli.completion_range({}) == "@{upstream}..HEAD"  # one commit ahead of the upstream
    subprocess.run(["git", "branch", "-f", "base", "HEAD"], check=True, capture_output=True)
    assert cli.completion_range({}) is None  # up to date: the push sends nothing


def test_an_up_to_date_push_abstains(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo_with_diff(tmp_path)
    monkeypatch.chdir(repo)
    branch = subprocess.run(
        ["git", "branch", "--show-current"], check=True, capture_output=True, text=True
    ).stdout.strip()
    subprocess.run(["git", "branch", "base", "HEAD"], check=True, capture_output=True)
    subprocess.run(["git", "branch", "--set-upstream-to=base", branch], check=True, capture_output=True)
    claims = repo / "claims.txt"
    claims.write_text("it works\n", encoding="utf-8")
    code = completion_hook_main(
        [],
        text=json.dumps({"tool_name": "Bash", "tool_input": {"command": "git push"}}),
        environ={
            "JEV_COMPLETION_CLAIMS": str(claims),
            "JEV_COMPLETION_TESTS": str(repo / "tests.log"),
            "JEV_HOOK_REQUIRED": "1",
        },
        provider=FakeProvider({}),
    )
    captured = capsys.readouterr()
    assert code == 0 and captured.out == ""
    assert "nothing to gate" in captured.err
