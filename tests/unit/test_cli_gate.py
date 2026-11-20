"""The harness-agnostic CLI refuses paths it must not read, and does not print allow."""

import json
import re
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Any, override

import anyio
import pytest

from jev_judge_mcp import cli, git
from jev_judge_mcp.cli import completion_hook_main, completion_matches, gate_main, judge_main
from jev_judge_mcp.domain import JsonValue, Question
from jev_judge_mcp.git import split_unified
from jev_judge_mcp.limits import GATE
from jev_judge_mcp.providers.base import Evaluation
from jev_judge_mcp.security import SecurityGuard
from jev_judge_mcp.tools import file_state
from tests.support.git_repo import repo_with_diff
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


def test_gate_replaces_undecodable_bytes_in_the_tests_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = repo_with_diff(tmp_path)
    monkeypatch.chdir(repo)
    (repo / "tests.log").write_bytes(b"good \xff")
    provider = FakeProvider({})
    result = cli.run_gate(["--diff", "HEAD~1", "--claims", "claims.json", "--tests", "tests.log"], provider=provider)
    assert result.exit_code == 0
    assert any(isinstance(state, dict) and state.get("tests") == "good �" for state, _ in provider.requests)


@pytest.mark.parametrize(("args", "tool"), [(["judge", "jev_verify"], "jev_verify"), (["gate"], "jev_gate")])
@pytest.mark.parametrize(
    ("variable", "value", "code", "message"),
    [
        ("JEV_API_KEY", "xy", "auth", "shorter than"),
        ("JEV_MCP_HTTP_PORT", "invalid-port-value", "invalid_arguments", "integer"),
    ],
)
def test_cli_configuration_failures_emit_a_result(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    args: list[str],
    tool: str,
    variable: str,
    value: str,
    code: str,
    message: str,
) -> None:
    import sys

    from jev_judge_mcp.__main__ import main

    monkeypatch.setenv(variable, value)
    monkeypatch.setattr(sys, "argv", ["jev-judge-mcp", *args])
    with pytest.raises(SystemExit) as caught:
        main()
    assert caught.value.code == 1
    captured = capsys.readouterr()
    result = json.loads(captured.out)
    assert result["tool"] == tool and result["action"] is None and result["unresolved"]
    assert result["error"]["code"] == code
    assert variable in result["error"]["message"] and message in result["error"]["message"]
    assert len(captured.err.splitlines()) == 1 and variable in captured.err
    assert value not in captured.out + captured.err


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
    assert envelope["error"]["code"] == "path_outside_scope"
    assert envelope["action"] is None


def test_cli_names_the_budget_when_git_stops_reading_an_oversized_diff(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = repo_with_diff(tmp_path)
    (repo / "x.py").write_text("x" * (4 * GATE.aggregate_evidence_units), encoding="utf-8")
    monkeypatch.chdir(repo)
    provider = FakeProvider({})
    code = gate_main(["--diff", "HEAD", "--claims", "claims.json", "--tests", "tests.log"], provider=provider)
    error = json.loads(capsys.readouterr().out)["error"]
    assert code == 2
    assert error["code"] == "input_too_large"
    assert not provider.requests


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
    repo = repo_with_diff(tmp_path)
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
    assert "path_outside_scope" in captured.out
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
    repo = repo_with_diff(tmp_path)
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
        pytest.param("make test\ngit push", True, id="newline"),
        pytest.param("TRACE=on git push", True, id="assignment"),
        pytest.param("if true; then git push; fi", True, id="then"),
        pytest.param("time git push", True, id="time"),
        pytest.param("command git push", True, id="command"),
        pytest.param("/usr/bin/git push", True, id="absolute-git"),
        pytest.param("echo 'x\ngit push'", False, id="quoted-newline"),
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
    repo = repo_with_diff(tmp_path)
    subdir = repo / "nested"
    subdir.mkdir()
    monkeypatch.chdir(subdir)
    raw = (repo / "tests.log").read_bytes()
    provider = FakeProvider({})
    code = gate_main(
        ["--diff", "HEAD~1", "--claims", "../claims.json", "--tests", "../tests.log"],
        provider=provider,
    )
    captured = capsys.readouterr()
    assert code == 0
    review = json.loads(captured.out)["payload"]["review"]
    assert review["reviewed_files"] == ["x.py"]
    assert "tests_weight" not in review
    assert provider.requests
    state = provider.requests[0][0]
    assert isinstance(state, dict)
    assert state.get("tests") == raw.decode("utf-8")


def test_a_path_containing_space_b_slash_is_split_at_the_header_midpoint() -> None:
    """The midpoint finds the full path, and the canonical parser requires symmetric headers."""
    same = "diff --git a/dir b/c b/dir b/c\nindex 1..2 100644\n--- a/dir b/c\n+++ b/dir b/c\t\n@@ -1 +1 @@\n-x\n+y\n"
    files = split_unified(same)
    assert files is not None and [item["path"] for item in files] == ["dir b/c"]
    renamed = (
        "diff --git a/old b/new\nsimilarity index 90%\nrename from old\nrename to new\n"
        "--- a/old\n+++ b/new\n@@ -1 +1 @@\n-x\n+y\n"
    )
    assert split_unified(renamed) is None


def test_the_completion_range_is_what_the_push_sends(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Without `JEV_COMPLETION_DIFF`: the commits ahead of the upstream, else the last commit; an
    empty range means nothing is being pushed and the hook abstains (ADR-0064 amendment)."""
    repo = repo_with_diff(tmp_path)
    monkeypatch.chdir(repo)
    assert cli.completion_range({"JEV_COMPLETION_DIFF": "HEAD~1"}, repo) == "HEAD~1"
    assert cli.completion_range({}, repo) == "HEAD~1..HEAD"  # no upstream yet: the last commit
    branch = subprocess.run(
        ["git", "branch", "--show-current"], check=True, capture_output=True, text=True
    ).stdout.strip()
    subprocess.run(["git", "branch", "base", "HEAD~1"], check=True, capture_output=True)
    subprocess.run(["git", "branch", "--set-upstream-to=base", branch], check=True, capture_output=True)
    assert cli.completion_range({}, repo) == "@{upstream}..HEAD"  # one commit ahead of the upstream
    subprocess.run(["git", "branch", "-f", "base", "HEAD"], check=True, capture_output=True)
    assert cli.completion_range({}, repo) is None  # up to date: the push sends nothing


def test_an_up_to_date_push_abstains(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = repo_with_diff(tmp_path)
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


@pytest.mark.parametrize(
    ("case", "expected"),
    [
        ("store", "secret_file"),
        ("sensitive_label", "secret_file"),
        ("opened_store", "secret_file"),
        ("patch", "invalid_arguments"),
        ("claims_size", "file_too_large"),
    ],
)
def test_gate_guards_local_inputs_before_judgment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str, expected: str
) -> None:
    from tests.support.git_repo import commit_files

    repo = repo_with_diff(tmp_path)
    monkeypatch.chdir(repo)
    if case == "store":
        commit_files(repo, {".env": "inert fixture", "x.py": "x = 3"})
    elif case == "sensitive_label":
        monkeypatch.setenv("JEV_API_KEY", "private-label-fixture")
        commit_files(repo, {"private-label-fixture.txt": "inert fixture"})
    elif case == "opened_store":
        # Simulate an inconclusive preliminary path check on an ordinary test fixture.
        monkeypatch.setenv("JEV_MCP_KEY_FILE", str(repo / "tests.log"))

        def unrecognized_path(_path: Path, _guard: SecurityGuard) -> bool:
            return False

        monkeypatch.setattr(file_state, "is_secret_store", unrecognized_path)
    elif case == "patch":

        def unsplittable(_text: str) -> None:
            return None

        monkeypatch.setattr(git, "split_unified", unsplittable)
    else:
        path = repo / "claims.json"
        path.write_text(" " * (GATE.aggregate_evidence_units + 1) + path.read_text())
    provider = FakeProvider({})
    result = cli.run_gate(["--diff", "HEAD~1", "--claims", "claims.json", "--tests", "tests.log"], provider=provider)
    assert (result.exit_code, result.error_code) == (2, expected)
    if case == "sensitive_label":
        assert "private-label-fixture" not in result.rendered
    assert not provider.requests


@pytest.mark.parametrize("with_text", [True, False], ids=["mixed", "binary_only"])
def test_gate_holds_a_binary_file_for_local_review(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, with_text: bool
) -> None:
    from tests.support.git_repo import commit_files

    repo = repo_with_diff(tmp_path)
    monkeypatch.chdir(repo)
    commit_files(repo, {".gitattributes": "logo.png -diff\n"})
    commit_files(repo, {"logo.png": "pixels\n"})
    provider = FakeProvider({})
    rev = "HEAD~3" if with_text else "HEAD~1"
    result = cli.run_gate(["--diff", rev, "--claims", "claims.json", "--tests", "tests.log"], provider=provider)
    if not with_text:
        assert (result.exit_code, result.error_code) == (2, "binary_file")
        assert "logo.png" in json.loads(result.rendered)["error"]["message"]
        return
    assert (result.exit_code, result.error_code) == (0, None), result.rendered
    payload = json.loads(result.rendered)["payload"]
    assert result.action != "auto"
    assert payload["skipped"] == [{"path": "logo.png", "reason": "binary"}]
    assert payload["unreviewed_files"] == ["logo.png"]
    assert payload["review"]["reviewed_files"] == [".gitattributes", "x.py"]


def test_cli_filters_configured_values_from_local_evidence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = repo_with_diff(tmp_path)
    monkeypatch.chdir(repo)
    marker = "inert-redaction-fixture"
    monkeypatch.setenv("JEV_API_KEY", marker)
    (repo / "tests.log").write_text(marker)
    (repo / "claims.json").write_text(json.dumps({"request": "Review " + marker, "claims": [marker]}))
    (repo / "x.py").write_text(marker)
    provider = FakeProvider({})
    outcome = cli.run_gate(["--diff", "HEAD", "--claims", "claims.json", "--tests", "tests.log"], provider=provider)
    assert outcome.exit_code == 0 and provider.requests
    assert marker not in str(provider.requests)


def test_required_completion_hook_asks_when_the_gate_handler_crashes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    async def broken(_args: dict[str, Any], _runtime: cli.Runtime, **_held: object) -> Any:
        raise RuntimeError("gate handler crashed")

    monkeypatch.setattr(cli.gate, "handle", broken)
    monkeypatch.chdir(repo_with_diff(tmp_path))
    code = completion_hook_main(
        [],
        text=_PUSH,
        environ={
            "JEV_HOOK_REQUIRED": "1",
            "JEV_COMPLETION_DIFF": "HEAD~1",
            "JEV_COMPLETION_CLAIMS": "claims.json",
            "JEV_COMPLETION_TESTS": "tests.log",
        },
        provider=FakeProvider({}),
    )
    captured = capsys.readouterr()
    assert code == 0
    assert captured.out and _ask(captured.out) == "ask"
    assert "(internal)" in captured.out


def test_gate_resolves_evidence_from_the_shell_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = repo_with_diff(tmp_path)
    directory = repo / "subdir"
    directory.mkdir()
    (directory / "here.log").write_text("2 passed\n", encoding="utf-8")
    monkeypatch.chdir(directory)
    provider = FakeProvider({})
    assert gate_main(["--diff", "HEAD~1", "--claims", "../claims.json", "--tests", "here.log"], provider=provider) == 0
    assert provider.requests
    assert json.loads(capsys.readouterr().out)["error"] is None


@pytest.mark.parametrize("claims_format", ["json", "plain"])
def test_completion_claims_and_provider_deadline(
    claims_format: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = repo_with_diff(tmp_path)
    monkeypatch.chdir(repo)
    claim = "The patch sets x to 2."
    second = "The tests report one passing case."
    claims = repo / "claims.json"
    if claims_format == "plain":
        claims.write_text(f"{claim}\n\n{second}\n", encoding="utf-8")
    remaining = float("inf")

    class DeadlineProvider(FakeProvider):
        @override
        async def evaluate(
            self, state: JsonValue, questions: Mapping[str, Question], model: str, timeout: float | None
        ) -> Evaluation:
            nonlocal remaining
            remaining = anyio.current_effective_deadline() - anyio.current_time()
            return await super().evaluate(state, questions, model, timeout)

    provider = DeadlineProvider({})
    assert (
        completion_hook_main(
            [],
            text=_PUSH,
            environ={
                "JEV_COMPLETION_DIFF": "HEAD~1",
                "JEV_COMPLETION_CLAIMS": claims.name,
                "JEV_COMPLETION_TESTS": "tests.log",
            },
            provider=provider,
        )
        == 0
    )
    assert _ask(capsys.readouterr().out) == "ask"
    state = next(state for state, _ in provider.requests if isinstance(state, dict) and "claims" in state)
    assert claim in str(state)
    if claims_format == "plain":
        assert second in str(state) and "git push origin main" in str(state)
    else:
        assert "Set x to 2" in str(state)
    fragment = Path(__file__).resolve().parents[2] / "docs/harness/completion.hooks.json"
    timeout = json.loads(fragment.read_text())["hooks"]["PreToolUse"][0]["hooks"][0]["timeout"]
    assert 0 < remaining < timeout


@pytest.mark.parametrize(
    ("command", "repository"),
    [
        ("git -C a -C b push", "a/b"),
        ("git -C '' push", ""),
        ("git -C /srv/repo push", "/srv/repo"),
        ("git -c core.hooksPath=x push", ""),
        ("git -C $DIR push", None),
        ("git -c remote.origin.url=x push", None),
        ("git --git-dir=x push", None),
    ],
    ids=["chained", "empty", "absolute", "hooks-path", "dynamic", "other-config", "git-dir"],
)
def test_git_options_select_the_repository_and_keep_the_shell_directory(
    tmp_path: Path, command: str, repository: str | None
) -> None:
    """`-C` resolves like git (chained, empty, absolute); a target git options could redirect is unknown."""
    (target,) = cli.completion_targets(command, tmp_path)
    assert target.shell_cwd == tmp_path
    assert target.git_cwd == (None if repository is None else tmp_path / repository)


@pytest.mark.parametrize("command", ["cd repo && git push", "git -C repo push"])
def test_completion_uses_the_commands_repository(command: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = repo_with_diff(tmp_path)
    monkeypatch.chdir(tmp_path)
    provider = FakeProvider({})
    completion_hook_main(
        [],
        text=json.dumps({"tool_input": {"command": command}}),
        environ={"JEV_COMPLETION_CLAIMS": str(repo / "claims.json"), "JEV_COMPLETION_TESTS": str(repo / "tests.log")},
        provider=provider,
    )
    assert any("+x = 2" in str(state) for state, _ in provider.requests)


@pytest.mark.parametrize("command", ["gh pr create", "gh pr merge"])
def test_completion_judges_the_branch_against_its_base_after_push(
    command: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = repo_with_diff(tmp_path)
    monkeypatch.chdir(repo)
    for args in (
        ("branch", "pushed", "HEAD"),
        ("branch", "--set-upstream-to=pushed"),
        ("update-ref", "refs/remotes/origin/main", "HEAD~1"),
        ("symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main"),
    ):
        subprocess.run(["git", *args], check=True, capture_output=True)
    provider = FakeProvider({})
    completion_hook_main(
        [],
        text=json.dumps({"tool_input": {"command": command}}),
        environ={"JEV_COMPLETION_CLAIMS": "claims.json", "JEV_COMPLETION_TESTS": "tests.log"},
        provider=provider,
    )
    assert any("+x = 2" in str(state) for state, _ in provider.requests)


def test_gate_reports_a_missing_tests_file_by_type(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(repo_with_diff(tmp_path))
    provider = FakeProvider({})
    result = cli.run_gate(["--diff", "HEAD~1", "--claims", "claims.json", "--tests", "missing.log"], provider=provider)
    assert (result.exit_code, result.error_code) == (2, "not_found")
    assert not provider.requests
