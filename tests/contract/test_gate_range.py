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
from tests.support.jev import NO_DEFECT, call_tool, choice

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
    # The directory is named so a caller can see a server launched outside its project.
    assert outcome.text == f"{message} (server working directory: {Path.cwd()})"
    assert outcome.requests == []


async def test_outside_a_git_repo_is_refused_without_a_provider_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    outcome = await call_tool("jev_gate_range", gate_args(), {})
    assert outcome.is_error
    assert outcome.code == "invalid_arguments"
    assert outcome.text == f"gate refuses to run outside a git repo (server working directory: {Path.cwd()})"
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


@pytest.mark.parametrize(("probability", "action"), [(0.95, "review"), (0.1, "review"), (None, "escalate")])
async def test_withheld_files_never_stand_on_a_siblings_judgment(
    repo: Path, probability: float | None, action: str
) -> None:
    commit_files(repo, {".env": "placeholder", "prod.env": "placeholder", "x.py": "x = 2"})
    answers = {
        "correctness": {"score": 2, "confidence": 0.95},
        "spec_match": {"score": 2, "confidence": 0.95},
        "test_gap": {"score": 0, "confidence": 0.95},
        "blast_radius": {"score": 0, "confidence": 0.95},
        "safe_to_apply": {"noul": probability},
        "defect_kind": NO_DEFECT,
        "source_0": choice("ci", "ci", "diff_x.py", "none"),
        "claim_0": {
            "choice": "verified",
            "confidence": 0.95,
            "probabilities": {"verified": 0.95, "contradicted": 0.03, "unsupported": 0.02},
        },
    }
    outcome = await call_tool("jev_gate_range", gate_args(), answers)
    body = outcome.payload
    assert body["action"] == action
    assert body["verification"]["action"] == "auto"
    assert body["partial"] and set(body["unreviewed_files"]) == {".env", "prod.env"}
    assert "accepted" not in body["reason_codes"] and "incomplete_context" in body["reason_codes"]
    assert not any("Proceed" in check for check in body["next_checks"])
    assert {b["path"] for b in body["blockers"] if b["code"] == "file_withheld"} == {".env", "prod.env"}
    assert all(item["reason"] == "secret_file" for item in body["skipped"])
    assert "placeholder" not in _sent_text(outcome)


async def test_a_range_of_only_secret_stores_is_refused_without_a_provider_call(repo: Path) -> None:
    commit_files(repo, {"prod.env": "TOKEN=abc\n"})
    outcome = await call_tool("jev_gate_range", gate_args(), {})
    assert outcome.is_error
    assert outcome.code == "secret_file" and "prod.env" in outcome.text
    assert outcome.requests == []


@pytest.mark.parametrize(
    ("key", "value"),
    [
        pytest.param("color.diff", "always", id="forced-color"),
        pytest.param("diff.noprefix", "true", id="no-prefix"),
        pytest.param("diff.mnemonicPrefix", "true", id="mnemonic-prefix"),
        pytest.param("diff.external", "false", id="external-driver"),
        pytest.param("diff.context", "0", id="zero-context"),
    ],
)
async def test_the_operators_git_settings_do_not_reshape_the_diff(repo: Path, key: str, value: str) -> None:
    commit_files(repo, {"x.py": "before\nx = 1\nafter\n"})
    commit_files(repo, {"x.py": "before\nx = 2\nafter\n"})
    set_config(repo, key, value)
    outcome = await call_tool("jev_gate_range", gate_args(), {})
    assert not outcome.is_error, outcome.text
    assert outcome.payload["review"]["reviewed_files"] == ["x.py"]
    review_state = cast(dict[str, object], outcome.requests[0][0])
    diff = cast(str, review_state["diff"])
    assert "-x = 1\n+x = 2\n" in diff
    assert " before\n" in diff and " after\n" in diff
    assert "\x1b" not in diff


async def test_large_range_packs_claims_and_accounts_for_every_call(repo: Path) -> None:
    files = {f"{i}.txt": (f"line {i} " + "x" * 92 + "\n") * size for i, size in enumerate((220, 110, 110))}
    commit_files(repo, files)
    outcome = await call_tool("jev_gate_range", gate_args(), {})
    assert not outcome.is_error, outcome.text
    assert outcome.payload["review"]["reviewed_files"] == list(files)
    groups = [state for state, _ in outcome.requests if isinstance(state, dict) and "claims" in state]
    assert len(groups) == 2
    patches = [
        item["id"]
        for group in groups
        for item in cast(list[dict[str, str]], group["evidence"])
        if item.get("kind") == "diff"
    ]
    assert patches == ["diff_0.txt", "diff_1.txt", "diff_2.txt"]
    assert outcome.payload["usage"] == {"input_tokens": 5, "output_tokens": 5}


@pytest.mark.parametrize(
    ("name", "reason"),
    [("extra.txt", "untracked"), ("hidden.txt", "binary"), ("large.txt", "oversize")],
)
async def test_incomplete_worktree_range_names_each_withheld_file(repo: Path, name: str, reason: str) -> None:
    commit_files(repo, {"hidden.txt": "before\n", ".gitignore": "ignored.txt\n.gitattributes\n"})
    (repo / "x.py").write_text("x = 3\n")
    (repo / "ignored.txt").touch()
    if reason == "binary":
        (repo / ".gitattributes").write_text("hidden.txt -diff\n")
    if reason == "oversize":
        commit_files(repo, {name: "before\n"})
    (repo / name).write_text("changed\n" if reason != "oversize" else "x" * GATE.doc_units)
    for fixture in ("claims.json", "tests.log"):
        (repo / fixture).unlink()

    outcome = await call_tool("jev_gate_range", gate_args("HEAD"), {})
    assert not outcome.is_error, outcome.text
    assert outcome.payload["skipped"] == [{"path": name, "reason": reason}]
    assert outcome.payload["partial"] and outcome.payload["action"] != "auto"
    assert outcome.payload["unreviewed_files"] == [name]
    assert outcome.payload["review"]["reviewed_files"] == ["x.py"]
    if reason == "untracked":
        assert any("git add -N extra.txt" in check for check in outcome.payload["next_checks"])


async def test_foreign_git_dir_cannot_redirect_a_gate_range(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    foreign_home = repo.parent / "foreign"
    foreign_home.mkdir()
    foreign = repo_with_diff(foreign_home)
    commit_files(foreign, {"other.txt": "this belongs to another repository\n"})
    monkeypatch.setenv("GIT_DIR", str(foreign / ".git"))
    outcome = await call_tool("jev_gate_range", gate_args(), {})
    assert not outcome.is_error, outcome.text
    assert outcome.payload["review"]["reviewed_files"] == ["x.py"]
    assert "other.txt" not in _sent_text(outcome)


async def test_a_quoted_header_preserves_the_filename(repo: Path) -> None:
    commit_files(repo, {'say "hi".py': "x = 3\n"})
    outcome = await call_tool("jev_gate_range", gate_args(), {})
    assert not outcome.is_error, outcome.text
    assert outcome.payload["review"]["reviewed_files"] == ['say "hi".py']


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
        'for arg in "$@"; do if [ "$arg" = "diff" ]; then head -c 20000000 /dev/zero | tr "\\000" y; '
        "exec sleep 60; fi; done\n"
        f'exec {real_git} "$@"\n',
        encoding="utf-8",
    )
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", f"{fake.parent}:{os.environ['PATH']}")
    monkeypatch.setattr("jev_judge_mcp.git.GIT_TIMEOUT_SECONDS", 20)
    started = time.monotonic()
    outcome = await call_tool("jev_gate_range", gate_args(), {})
    assert time.monotonic() - started < 10
    assert outcome.code == "input_too_large"
    assert outcome.requests == []


async def test_scout_016_git_quoted_paths_are_reviewed(repo: Path) -> None:
    names = ["café.txt", 'quoted"name.txt', "two words.txt"]
    commit_files(repo, {name: "plain\n" for name in names})
    result = await call_tool("jev_gate_range", gate_args(), {})
    assert not result.is_error, result.text
    assert sorted(result.payload["review"]["reviewed_files"]) == sorted(names)
    (repo / names[0]).unlink()
    deleted = await call_tool("jev_gate_range", gate_args("HEAD"), {})
    assert not deleted.is_error and deleted.payload["review"]["reviewed_files"] == [names[0]]


async def test_scout_004_configured_key_path_refused(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = repo / "custom-store"
    commit_files(repo, {path.name: ""})
    monkeypatch.setenv("JEV_MCP_KEY_FILE", str(path))
    for args in [gate_args(), gate_args(tests_path=path.name)]:
        result = await call_tool("jev_gate_range", args, {})
        assert result.code == "secret_file" and not result.requests


async def test_scout_016_incomplete_quoted_header_is_refused(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from jev_judge_mcp.git import split_unified
    from jev_judge_mcp.tools import gate_range

    header = 'diff --git "a/ordinary" "b/ordinary'
    assert split_unified(header) is None

    def malformed(*args: object, **kwargs: object) -> str:
        return header

    monkeypatch.setattr(gate_range, "diff_argument", malformed)
    outcome = await call_tool("jev_gate_range", gate_args(), {})
    assert outcome.code == "invalid_arguments" and not outcome.requests


@pytest.mark.parametrize("context_override", [False, True])
async def test_range_withholds_partial_pem_and_masks_exact_values(
    repo: Path, monkeypatch: pytest.MonkeyPatch, context_override: bool
) -> None:
    if context_override:
        monkeypatch.setenv("GIT_DIFF_OPTS", "--unified=1")
    lines = ["-----BEGIN PRIVATE KEY-----", *["unchanged fixture line"] * 15, "-----END PRIVATE KEY-----"]
    commit_files(repo, {"notes.txt": "\n".join(lines)})
    lines[7] = "changed fixture line"
    commit_files(
        repo, {"notes.txt": "\n".join(lines), "ok.txt": "safe context configured-fixture-marker stored-fixture-marker"}
    )
    monkeypatch.setenv("JEV_API_KEY", "configured-fixture-marker")
    store = repo / "fixture-store"
    store.write_text("stored-fixture-marker")
    monkeypatch.setenv("JEV_MCP_KEY_FILE", str(store))
    result = await call_tool("jev_gate_range", gate_args(), {})
    assert not result.is_error, result.text
    assert result.payload.get("skipped") == [{"path": "notes.txt", "reason": "secret_file"}]
    sent = _sent_text(result)
    assert "safe context" in sent
    assert all(marker not in sent for marker in ("fixture line", "configured-fixture-marker", "stored-fixture-marker"))


@pytest.mark.parametrize("revision", ["HEAD~1..HEAD", "HEAD~1...HEAD", "HEAD~1", "HEAD~1.."])
async def test_subdirectory_range_headers_keep_the_repository_origin(
    repo: Path, monkeypatch: pytest.MonkeyPatch, revision: str
) -> None:
    commit_files(repo, {"sub/notes.txt": "ordinary fixture", "outside.txt": "outside scope"})
    set_config(repo, "diff.relative", "true")
    monkeypatch.chdir(repo / "sub")
    result = await call_tool("jev_gate_range", gate_args(revision), {})
    assert not result.is_error, result.text
    assert result.payload["review"]["reviewed_files"] == ["sub/notes.txt"]
    assert "outside scope" not in _sent_text(result)


async def test_subtree_range_uses_physical_origin_for_store_check(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    commit_files(repo, {"sub/ordinary": "benign fixture"})
    monkeypatch.setenv("JEV_MCP_KEY_FILE", str(repo / "sub/ordinary"))
    result = await call_tool("jev_gate_range", gate_args("4b825dc642cb6eb9a060e54bf8d69288fbee4904..HEAD:sub"), {})
    assert result.code == "secret_file" and not result.requests


async def test_sensitive_range_path_is_masked_in_skips_and_withheld_from_provider(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    marker = "configured-fixture-marker"
    monkeypatch.setenv("JEV_API_KEY", marker)
    name = f"notes-{marker}.txt"
    commit_files(repo, {name: "private fixture", "ordinary.txt": "public fixture"})
    result = await call_tool("jev_gate_range", gate_args(), {})
    assert not result.is_error
    assert result.payload.get("skipped") == [{"path": "notes-[redacted].txt", "reason": "secret_file"}]
    assert "public fixture" in _sent_text(result)
    assert marker not in _sent_text(result)


async def test_an_unattributed_subtree_object_is_refused(repo: Path) -> None:
    commit_files(repo, {"nested/ordinary.txt": "fixture"})
    tree = _git_out(repo, "rev-parse", "HEAD:nested")
    result = await call_tool("jev_gate_range", gate_args(f"HEAD~1..{tree}"), {})
    assert result.code == "invalid_arguments" and not result.requests


async def test_working_tree_change_between_inspections_refuses(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from jev_judge_mcp import git

    original = git.split_unified
    target = repo / "x.py"
    target.write_text("first working version\n")

    def read(diff: str) -> list[dict[str, str]] | None:
        patch = original(diff)
        target.write_text("second working version\n")
        return patch

    monkeypatch.setattr(git, "split_unified", read)
    result = await call_tool("jev_gate_range", gate_args("HEAD"), {})
    assert result.code == "invalid_arguments" and not result.requests
