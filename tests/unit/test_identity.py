"""Build identity: plain version from a wheel or the release tag, local suffix otherwise (ADR-0054)."""

import logging
import re
import subprocess
import sys
from collections.abc import Callable
from importlib.metadata import version
from pathlib import Path
from threading import Event

import anyio
import pytest
from mcp import Client
from mcp.types import CallToolResult

from jev_judge_mcp import identity
from jev_judge_mcp.identity import DISTRIBUTION, reported_version
from jev_judge_mcp.server import JevMCPServer
from jev_judge_mcp.settings import Settings
from jev_judge_mcp.tools import TOOLS, Runtime, Toolset
from tests.support.git_repo import commit_files
from tests.support.jev import FakeProvider, text_of

INSTALLED = version(DISTRIBUTION)
HEAD = "a" * 40
OTHER = "b" * 40
TAG_OBJECT = "c" * 40
PEELED = "d" * 40


def _manifest(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "pyproject.toml").write_text(f'[project]\nname = "{DISTRIBUTION}"\nversion = "0.1.1"\n', encoding="utf-8")


def _start(root: Path) -> Path:
    path = root / "src" / "jev_judge_mcp" / "identity.py"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("", encoding="utf-8")
    return path


def _git(root: Path, head: str, tag: str | None) -> Path:
    """A normal `.git` directory. `tag` is the commit `v<version>` names, when set."""
    _manifest(root)
    git = root / ".git"
    git.mkdir()
    (git / "HEAD").write_text(head + "\n", encoding="utf-8")
    if tag is not None:
        ref = git / "refs" / "tags" / f"v{INSTALLED}"
        ref.parent.mkdir(parents=True)
        ref.write_text(tag + "\n", encoding="utf-8")
    return git


def test_a_tree_that_is_not_this_distribution_reports_the_plain_version(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "other"\n', encoding="utf-8")
    git = tmp_path / ".git"
    git.mkdir()
    (git / "HEAD").write_text(OTHER + "\n", encoding="utf-8")
    assert reported_version(_start(tmp_path)) == INSTALLED


def test_a_wheel_path_inside_the_checkout_reports_the_plain_version(tmp_path: Path) -> None:
    git = _git(tmp_path, OTHER, HEAD)
    assert git.is_dir()
    start = tmp_path / ".venv" / "lib" / "site-packages" / "jev_judge_mcp" / "identity.py"
    start.parent.mkdir(parents=True)
    start.write_text("", encoding="utf-8")
    assert reported_version(start) == INSTALLED


def test_no_git_directory_reports_the_plain_version(tmp_path: Path) -> None:
    _manifest(tmp_path)
    assert reported_version(_start(tmp_path)) == INSTALLED


def test_head_on_the_release_tag_reports_the_plain_version(tmp_path: Path) -> None:
    _git(tmp_path, HEAD, HEAD)
    assert reported_version(_start(tmp_path)) == INSTALLED


def test_head_off_the_release_tag_gains_the_local_suffix(tmp_path: Path) -> None:
    _git(tmp_path, OTHER, HEAD)
    assert reported_version(_start(tmp_path)) == f"{INSTALLED}+g{OTHER[:7]}"


def test_a_missing_release_tag_is_not_that_tag(tmp_path: Path) -> None:
    _git(tmp_path, HEAD, None)
    assert reported_version(_start(tmp_path)) == f"{INSTALLED}+g{HEAD[:7]}"


def test_a_branch_ref_and_packed_refs_tag(tmp_path: Path) -> None:
    git = _git(tmp_path, "ref: refs/heads/main", None)
    (git / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    ref = git / "refs" / "heads" / "main"
    ref.parent.mkdir(parents=True)
    ref.write_text(OTHER + "\n", encoding="utf-8")
    (git / "packed-refs").write_text(
        f"# pack-refs with: peeled fully-peeled sorted\n{HEAD} refs/tags/v{INSTALLED}\n",
        encoding="utf-8",
    )
    assert reported_version(_start(tmp_path)) == f"{INSTALLED}+g{OTHER[:7]}"


def test_a_loose_ref_wins_over_a_stale_packed_ref(tmp_path: Path) -> None:
    git = _git(tmp_path, "ref: refs/heads/main", None)
    (git / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    ref = git / "refs" / "heads" / "main"
    ref.parent.mkdir(parents=True)
    ref.write_text(OTHER + "\n", encoding="utf-8")
    (git / "packed-refs").write_text(
        f"{HEAD} refs/heads/main\n{HEAD} refs/tags/v{INSTALLED}\n",
        encoding="utf-8",
    )
    assert reported_version(_start(tmp_path)) == f"{INSTALLED}+g{OTHER[:7]}"


def test_a_peeled_packed_tag_matches_the_commit_not_the_tag_object(tmp_path: Path) -> None:
    git = _git(tmp_path, PEELED, None)
    (git / "packed-refs").write_text(
        f"{TAG_OBJECT} refs/tags/v{INSTALLED}\n^{PEELED}\n",
        encoding="utf-8",
    )
    assert reported_version(_start(tmp_path)) == INSTALLED


def test_a_worktree_git_file_reads_the_common_dir(tmp_path: Path) -> None:
    _manifest(tmp_path)
    common = tmp_path / "common"
    work = common / "worktrees" / "wt"
    work.mkdir(parents=True)
    (work / "HEAD").write_text("ref: refs/heads/topic\n", encoding="utf-8")
    (work / "commondir").write_text("../..\n", encoding="utf-8")
    (work / "gitdir").write_text(str(tmp_path / ".git") + "\n", encoding="utf-8")
    ref = common / "refs" / "heads" / "topic"
    ref.parent.mkdir(parents=True)
    ref.write_text(OTHER + "\n", encoding="utf-8")
    (common / "packed-refs").write_text(f"{HEAD} refs/tags/v{INSTALLED}\n", encoding="utf-8")
    (tmp_path / ".git").write_text(f"gitdir: {work}\n", encoding="utf-8")
    assert reported_version(_start(tmp_path)) == f"{INSTALLED}+g{OTHER[:7]}"


def test_an_unreadable_head_stays_the_plain_version(tmp_path: Path) -> None:
    git = _git(tmp_path, OTHER, HEAD)
    (git / "HEAD").unlink()
    (git / "HEAD").mkdir()
    assert reported_version(_start(tmp_path)) == INSTALLED


def test_this_checkout_reports_a_pep440_identity() -> None:
    identity = reported_version()
    suffix = identity.removeprefix(f"{INSTALLED}+g")
    assert identity == INSTALLED or (identity.startswith(f"{INSTALLED}+g") and re.fullmatch(r"[0-9a-f]{7}", suffix))


def test_version_flag_prints_the_same_identity() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "jev_judge_mcp", "--version"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == f"jev-judge-mcp {reported_version()}\n"
    assert result.stderr == ""


@pytest.mark.parametrize("flag", ["--help", "-h"])
def test_help_flag_prints_usage_and_exits_zero_without_serving(flag: str) -> None:
    """`--help`/`-h` print every subcommand and exit 0; the server never starts."""
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import runpy, sys; sys.modules['jev_judge_mcp.server'] = None; "
            "runpy.run_module('jev_judge_mcp', run_name='__main__')",
            flag,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "usage: jev-judge-mcp" in result.stdout
    for subcommand in ("install", "hook", "doctor", "setup", "judge", "gate", "completion-hook"):
        assert subcommand in result.stdout, subcommand
    assert result.stderr == ""


async def _until_version(
    client: Client, args: dict[str, object], matches: Callable[[dict[str, object]], bool]
) -> CallToolResult:
    with anyio.fail_after(2):
        while True:
            result = await client.call_tool("jev_gate", args)
            if matches((result.meta or {})["jev/version"]):
                return result
            await anyio.sleep(0.01)
    raise AssertionError("version observation did not arrive")


@pytest.mark.anyio
async def test_every_result_reports_loaded_version_and_source_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "subject"
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    _manifest(repo)
    module = _start(repo)
    commit_files(
        repo,
        {
            "pyproject.toml": (repo / "pyproject.toml").read_text(),
            str(module.relative_to(repo)): "clean = True",
        },
    )
    sha = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()
    monkeypatch.setattr(identity, "__file__", str(module))
    foreign = tmp_path / "foreign"
    subprocess.run(["git", "init", "-q", str(foreign)], check=True)
    commit_files(foreign, {"other.txt": "foreign"})
    module.write_text("dirty = 1")
    with monkeypatch.context() as isolated:
        isolated.setenv("GIT_DIR", str(foreign / ".git"))
        service = JevMCPServer(
            toolset=Toolset(Runtime(Settings(), provider_factory=lambda _: FakeProvider({})), TOOLS),
            log_level="WARNING",
        )
    try:
        async with Client(service) as client:
            args: dict[str, object] = {"request": "r", "diff": "+x", "claims": ["x"], "evidence": "e"}
            initial = await client.call_tool("jev_gate", args)
            info = (initial.meta or {})["jev/version"]
            assert info["loaded"]["commit"] == sha and info["loaded"]["dirty"] is True
            assert info["restart_needed"] is False
            module.write_text("dirty = 2")
            changed = await _until_version(client, args, lambda current: current["restart_needed"] is True)
            assert (changed.meta or {})["jev/version"]["restart_needed"] is True
            assert text_of(changed) == text_of(initial)
            assert "restart" in changed.content[-1].model_dump()["text"].lower()
            commit_files(repo, {str(module.relative_to(repo)): "clean = False"})
            repeated = await _until_version(client, {}, lambda current: current["current_commit"] != sha)
            assert repeated.is_error and (repeated.meta or {})["jev/version"]["loaded"] == info["loaded"]
            assert (repeated.meta or {})["jev/version"]["current_commit"] != sha
            assert "restart" in repeated.content[-1].model_dump()["text"].lower()
    finally:
        await service.toolset.aclose()

    restarted = identity.ServerVersion().field()
    assert restarted["loaded"] == {
        "package": None,
        "commit": (repeated.meta or {})["jev/version"]["current_commit"],
        "dirty": False,
    }
    assert restarted["restart_needed"] is False
    (repo / ".git" / "HEAD").write_bytes(bytes([255]))
    unavailable = identity.ServerVersion().field()
    assert unavailable["restart_needed"] is True and "cannot verify" in str(unavailable["notice"])


@pytest.mark.anyio
async def test_installed_version_changes_are_reported(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    module = tmp_path / "site-packages" / "jev_judge_mcp" / "identity.py"
    module.parent.mkdir(parents=True)
    module.touch()
    monkeypatch.setattr(identity, "__file__", str(module))
    installed = "1.0"

    def installed_version(distribution: str) -> str:
        assert distribution == DISTRIBUTION
        if not installed:
            raise OSError("metadata is unreadable")
        return installed

    monkeypatch.setattr(identity, "version", installed_version)
    loaded = identity.ServerVersion()
    assert loaded.field()["restart_needed"] is False
    installed = "1.1"
    async with anyio.create_task_group() as tasks:
        loaded.refresh(tasks)
    changed = loaded.field()
    assert changed["loaded"] == {"package": "1.0", "commit": None, "dirty": None}
    assert changed["current_package"] == "1.1" and changed["restart_needed"] is True
    installed = ""
    async with anyio.create_task_group() as tasks:
        loaded.refresh(tasks)
    assert "cannot verify" in str(loaded.field()["notice"])


@pytest.mark.anyio
async def test_served_version_uses_completed_refreshes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _git(tmp_path, HEAD, None)
    module = _start(tmp_path)
    monkeypatch.setattr(identity, "__file__", str(module))
    now = 0.0
    monkeypatch.setattr(identity, "monotonic", lambda: now)
    entered = Event()
    release = Event()
    hold_scan = False
    scans = 0
    source_stamp = identity._source_stamp  # pyright: ignore[reportPrivateUsage]

    def scan(root: Path) -> tuple[tuple[str, int, int, int], ...]:
        nonlocal scans
        scans += 1
        if hold_scan:
            entered.set()
            release.wait()
        return source_stamp(root)

    monkeypatch.setattr(identity, "_source_stamp", scan)
    service = JevMCPServer(
        toolset=Toolset(Runtime(Settings(), provider_factory=lambda _: FakeProvider({})), TOOLS), log_level="WARNING"
    )
    try:
        async with Client(service) as client:
            try:
                await anyio.sleep(0.15)
                assert scans == 1
                hold_scan = True
                args: dict[str, object] = {"request": "r", "diff": "+x", "claims": ["x"], "evidence": "e"}
                with anyio.fail_after(2):
                    recent = await client.call_tool("jev_gate", args)
                assert (recent.meta or {})["jev/version"]["restart_needed"] is False

                with anyio.fail_after(2):
                    while not entered.is_set():
                        await anyio.sleep(0.01)
                now = 10.0
                module.write_text("changed = True")
                with anyio.fail_after(2):
                    pending = await client.call_tool("jev_gate", args)
                observation = (pending.meta or {})["jev/version"]
                assert observation["restart_needed"] is False and "notice" not in observation
                assert len(pending.content) == len(recent.content)
                assert scans == 2

                release.set()
                with anyio.fail_after(2):
                    while service.code_version.field()["restart_needed"] is not True:
                        await anyio.sleep(0.01)
                    changed = await client.call_tool("jev_gate", args)
                assert (changed.meta or {})["jev/version"]["restart_needed"] is True
                assert "source changed" in changed.content[-1].model_dump()["text"]
            finally:
                release.set()
    finally:
        await service.toolset.aclose()


@pytest.mark.anyio
async def test_a_failed_refresh_reports_an_unverifiable_installation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _git(tmp_path, HEAD, None)
    module = _start(tmp_path)
    monkeypatch.setattr(identity, "__file__", str(module))
    server = identity.ServerVersion()
    assert server.field()["restart_needed"] is False

    def broken(*_: object) -> str:
        raise RuntimeError("observation failed")

    monkeypatch.setattr(identity, "_resolve_ref", broken)
    caplog.set_level(logging.WARNING, logger="jev_judge_mcp.identity")
    async with anyio.create_task_group() as tasks:
        server.refresh(tasks)
    assert "cannot verify" in str(server.field()["notice"])
    assert "installation observation failed" in caplog.text


@pytest.mark.anyio
async def test_unreadable_source_retries_after_one_second(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _git(tmp_path, HEAD, None)
    module = _start(tmp_path)
    monkeypatch.setattr(identity, "__file__", str(module))
    now = 0.0
    monkeypatch.setattr(identity, "monotonic", lambda: now)
    server = identity.ServerVersion()
    assert server.field()["restart_needed"] is False
    package = module.parent.rename(module.parent.with_name("hidden"))
    async with anyio.create_task_group() as tasks:
        server.refresh(tasks)
    assert "cannot verify" in str(server.field()["notice"])
    package.rename(module.parent)
    now = 0.999
    async with anyio.create_task_group() as tasks:
        server.refresh(tasks)
    assert "cannot verify" in str(server.field()["notice"])
    now = 1.0
    async with anyio.create_task_group() as tasks:
        server.refresh(tasks)
    assert server.field()["restart_needed"] is False


@pytest.mark.anyio
async def test_large_source_tree_reports_unverifiable_identity(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _git(tmp_path, HEAD, None)
    module = _start(tmp_path)
    for n in range(1100):
        (module.parent / str(n)).touch()
    monkeypatch.setattr(identity, "__file__", str(module))
    server = identity.ServerVersion()
    for _ in range(3):
        async with anyio.create_task_group() as tasks:
            server.refresh(tasks)
        info = server.field()
        assert info["restart_needed"] is True and "cannot verify" in str(info["notice"])


@pytest.mark.parametrize("packed", [False, True])
def test_loose_release_ref_with_packed_tag_object(tmp_path: Path, packed: bool) -> None:
    from tests.support.git_repo import repo_with_diff

    repo = repo_with_diff(tmp_path)
    _manifest(repo)
    args = ["git", "-C", str(repo)]
    subprocess.run(
        [
            *args,
            "-c",
            "user.name=test",
            "-c",
            "user.email=test@example.invalid",
            "tag",
            "-a",
            f"v{INSTALLED}",
            "-m",
            "release",
        ],
        check=True,
    )
    ref = repo / ".git" / "refs" / "tags" / f"v{INSTALLED}"
    oid = ref.read_text().strip()
    objects = repo / ".git" / "objects"
    if packed:
        subprocess.run(
            [*args, "pack-objects", str(objects / "pack" / "pack")],
            input=oid + "\n",
            text=True,
            check=True,
            capture_output=True,
        )
        (objects / oid[:2] / oid[2:]).unlink()
    assert reported_version(_start(repo)) == INSTALLED
