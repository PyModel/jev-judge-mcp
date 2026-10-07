"""jev_gate_range, the extension that reads the gate's diff from git (ADR-0080).

Owned here: the range is read server-side and reaches the provider whole, the operator's git
settings cannot reshape what is read, secret stores never leave and credential literals are
redacted, the test log is read under the file tools' scope and is not self-reported, the payload
names this tool on both result shapes, and every git or path refusal is typed with no provider
call. The review, claim, and fail-closed behavior is jev_gate's handler, owned by its own tests; the header splitter is
`tests/unit/test_cli_gate.py`'s.
"""

import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, cast

import pytest

from jev_judge_mcp.limits import GATE
from tests.support.git_repo import commit_files, repo_with_diff, set_config
from tests.support.jev import call_tool

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.delenv("GIT_DIR", raising=False)
    monkeypatch.delenv("GIT_WORK_TREE", raising=False)
    root = repo_with_diff(tmp_path)
    monkeypatch.chdir(root)
    return root


def gate_args(rev_range: str = "HEAD~1..HEAD", **extra: Any) -> dict[str, Any]:
    return {
        "request": "Set x to 2",
        "range": rev_range,
        "claims": ["The patch sets x to 2."],
        "evidence": [{"id": "ci", "text": "1 passed"}],
        **extra,
    }


async def test_the_range_is_read_by_the_server_and_reviewed_whole(repo: Path) -> None:
    outcome = await call_tool("jev_gate_range", gate_args(tests_path="tests.log"), {})
    assert not outcome.is_error, outcome.text
    payload = outcome.payload
    assert payload["tool"] == "jev_gate_range"
    assert payload["review"]["reviewed_files"] == ["x.py"]
    # A log the server read is weighed as read (ADR-0067), not self-reported.
    assert "tests_weight" not in payload["review"]
    review_state = cast(dict[str, object], outcome.requests[0][0])
    assert "-x = 1\n+x = 2\n" in cast(str, review_state["diff"])
    assert review_state["tests"] == (repo / "tests.log").read_text(encoding="utf-8")


@pytest.mark.usefixtures("repo")
async def test_the_budget_refusal_names_this_tool() -> None:
    evidence = [{"id": f"e{index}", "text": "x"} for index in range(GATE.evidence_items + 1)]
    outcome = await call_tool("jev_gate_range", gate_args(evidence=evidence), {})
    assert outcome.is_error
    assert outcome.code == "input_too_large"
    assert outcome.payload["tool"] == "jev_gate_range"
    assert outcome.requests == []


@pytest.mark.parametrize(
    ("rev_range", "message"),
    [
        pytest.param("--output=/tmp/x", "refusing that git range", id="leading-dash"),
        pytest.param("HEAD\x00", "refusing that git range", id="nul"),
        pytest.param("no-such-rev", "refusing that git range", id="unknown-revision"),
        pytest.param("HEAD..HEAD", "git diff was empty", id="empty-diff"),
    ],
)
@pytest.mark.usefixtures("repo")
async def test_a_range_git_cannot_diff_is_refused_without_a_provider_call(rev_range: str, message: str) -> None:
    outcome = await call_tool("jev_gate_range", gate_args(rev_range), {})
    assert outcome.is_error
    assert outcome.code == "invalid_arguments"
    assert outcome.text == message
    assert outcome.requests == []


async def test_outside_a_git_repo_is_refused_without_a_provider_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    outcome = await call_tool("jev_gate_range", gate_args(), {})
    assert outcome.is_error
    assert outcome.code == "invalid_arguments"
    assert outcome.text == "gate refuses to run outside a git repo"
    assert outcome.requests == []


async def test_a_tests_path_outside_the_working_directory_is_refused(repo: Path) -> None:
    (repo.parent / "outside.log").write_text("1 passed\n", encoding="utf-8")
    outcome = await call_tool("jev_gate_range", gate_args(tests_path="../outside.log"), {})
    assert outcome.is_error
    assert outcome.code == "path_outside_scope"
    assert outcome.requests == []


_AWS_KEY = "AKIA" + "ABCDEFGHIJKLMNOP"
"""Built from two halves so this file carries no credential literal itself."""


def _sent_text(outcome: Any) -> str:
    return "\n".join(repr(request) for request in outcome.requests)


async def test_secret_stores_never_leave_and_literals_are_redacted(repo: Path) -> None:
    commit_files(repo, {".env": "DB_PASSWORD=hunter2-prod\n", "app.py": f'KEY_ID = "{_AWS_KEY}"\n'})
    outcome = await call_tool("jev_gate_range", gate_args(), {})
    assert not outcome.is_error, outcome.text
    assert outcome.payload["review"]["reviewed_files"] == ["app.py"]
    assert outcome.payload["skipped"] == [{"path": ".env", "reason": "secret_file"}]
    sent = _sent_text(outcome)
    assert "hunter2-prod" not in sent
    assert _AWS_KEY not in sent
    assert "KEY_ID" in sent


async def test_a_range_of_only_secret_stores_is_refused_without_a_provider_call(repo: Path) -> None:
    commit_files(repo, {"prod.env": "TOKEN=abc\n"})
    outcome = await call_tool("jev_gate_range", gate_args(), {})
    assert outcome.is_error
    assert outcome.code == "secret_file"
    assert outcome.requests == []


@pytest.mark.parametrize(
    ("key", "value"),
    [
        pytest.param("color.diff", "always", id="forced-color"),
        pytest.param("diff.noprefix", "true", id="no-prefix"),
        pytest.param("diff.mnemonicPrefix", "true", id="mnemonic-prefix"),
        pytest.param("diff.external", "false", id="external-driver"),
    ],
)
async def test_the_operators_git_settings_do_not_reshape_the_diff(repo: Path, key: str, value: str) -> None:
    set_config(repo, key, value)
    outcome = await call_tool("jev_gate_range", gate_args(), {})
    assert not outcome.is_error, outcome.text
    assert outcome.payload["review"]["reviewed_files"] == ["x.py"]
    review_state = cast(dict[str, object], outcome.requests[0][0])
    diff = cast(str, review_state["diff"])
    assert "-x = 1\n+x = 2\n" in diff
    assert "\x1b" not in diff


async def test_a_diff_whose_headers_do_not_split_is_refused(repo: Path) -> None:
    commit_files(repo, {'say "hi".py': "x = 3\n"})  # git quotes this header, so no path can be read
    outcome = await call_tool("jev_gate_range", gate_args(), {})
    assert outcome.is_error
    assert outcome.code == "invalid_arguments"
    assert outcome.requests == []


def _git_out(repo: Path, *args: str) -> str:
    run = subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True, input="")
    return run.stdout.strip()


async def test_a_range_naming_a_blob_is_refused_before_git_diff_runs(repo: Path) -> None:
    """A blob diff's header names the object id, not the file, so no path guard could see `.env`."""
    commit_files(repo, {".env": "DB_PASSWORD=hunter2-prod\n"})
    empty = _git_out(repo, "hash-object", "-w", "--stdin")
    outcome = await call_tool("jev_gate_range", gate_args(f"{empty}..{_git_out(repo, 'rev-parse', 'HEAD:.env')}"), {})
    assert outcome.is_error
    assert outcome.code == "invalid_arguments"
    assert outcome.requests == []


async def test_a_tree_path_end_is_accepted(repo: Path) -> None:
    """`rev:path` names a tree: the security corpus's range shape, which resolves in a shallow clone."""
    commit_files(repo, {"pkg/a.py": "a = 1\n"})
    outcome = await call_tool("jev_gate_range", gate_args("4b825dc642cb6eb9a060e54bf8d69288fbee4904..HEAD:pkg"), {})
    assert not outcome.is_error, outcome.text
    assert outcome.payload["review"]["reviewed_files"] == ["a.py"]


async def test_the_diff_stays_inside_the_working_directory(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    commit_files(repo, {"sub/a.py": "a = 1\n", "outside.py": "o = 1\n"})
    monkeypatch.chdir(repo / "sub")
    outcome = await call_tool("jev_gate_range", gate_args(), {})
    assert not outcome.is_error, outcome.text
    assert outcome.payload["review"]["reviewed_files"] == ["sub/a.py"]
    assert "o = 1" not in _sent_text(outcome)


async def test_a_renamed_secret_store_keeps_its_name(repo: Path) -> None:
    commit_files(repo, {".env": "DB_PASSWORD=hunter2-prod\nMODE=prod\n"})
    _git_out(repo, "mv", ".env", "notes.txt")
    commit_files(repo, {"notes.txt": "DB_PASSWORD=hunter2-prod\nMODE=dev\n"}, "rename")
    outcome = await call_tool("jev_gate_range", gate_args(), {})
    assert not outcome.is_error, outcome.text
    assert {"path": ".env", "reason": "secret_file"} in outcome.payload["skipped"]


async def test_a_diff_over_the_gate_budget_is_refused_without_reading_it_all(repo: Path) -> None:
    commit_files(repo, {"big.txt": "y" * 99 + "\n" * 1})
    commit_files(repo, {"big.txt": ("z" * 99 + "\n") * (GATE.aggregate_evidence_units * 3 // 100 + 10)})
    outcome = await call_tool("jev_gate_range", gate_args(), {})
    assert outcome.is_error
    assert outcome.code == "input_too_large"
    assert "narrow the range" in outcome.text  # git's read stopped at the budget, not the gate's check after it
    assert outcome.requests == []


async def test_a_flooding_git_is_killed_at_the_budget_not_read_to_its_end(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The read stops at the byte bound: a `git diff` that prints far past the budget and then never
    exits is killed there, long before the timeout, instead of being read to its end."""
    real_git = shutil.which("git")
    assert real_git is not None
    fake = tmp_path / "bin" / "git"
    fake.parent.mkdir()
    fake.write_text(
        "#!/bin/sh\n"
        'if [ "$3" = "diff" ]; then head -c 20000000 /dev/zero | tr "\\000" y; exec sleep 60; fi\n'
        f'exec {real_git} "$@"\n',
        encoding="utf-8",
    )
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", f"{fake.parent}:{os.environ['PATH']}")
    monkeypatch.setattr("jev_judge_mcp.git_diff.GIT_TIMEOUT_SECONDS", 20)
    started = time.monotonic()
    outcome = await call_tool("jev_gate_range", gate_args(), {})
    assert time.monotonic() - started < 10
    assert outcome.code == "input_too_large"
    assert outcome.requests == []
