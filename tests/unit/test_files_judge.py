"""The files-judge expansion and prune, at `plan`'s boundary, and the bounded fan-out at the handler's.

`plan` is the deterministic half: this file owns the filesystem mechanics (directories, globs,
symlinks, skip list, secret stores, binary, size, the hard cap) where `scope` is a parameter, the
same shape `tests/unit/test_file_state.py` uses for the single-file read. The fan-out tests drive
the real handler through a `Toolset` with an injected provider, because the behaviors they own —
the in-flight bound, a per-file failure that does not fail the batch, and the `auth` refusal — need
a provider the batch cannot know. `tests/contract/test_files_judge.py` owns the published surface.
"""

import asyncio
import json
from pathlib import Path
from typing import Any, cast, override

import pytest
from mcp.types import TextContent

from jev_judge_mcp.domain import JsonValue, Usage
from jev_judge_mcp.limits import FILES_JUDGE
from jev_judge_mcp.providers import Evaluation, ProviderConfigError, ProviderError
from jev_judge_mcp.settings import Settings
from jev_judge_mcp.tools import TOOLS, Runtime, Toolset
from jev_judge_mcp.tools.files_judge import plan
from tests.support.jev import TEST_GUARD, FakeProvider

PERMISSIVE = {"file": {"score": 0.2, "probabilities": {"0": 0.8, "1": 0.2}, "confidence": 0.95}}


def make(path: Path, data: bytes | str = "content") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, str):
        path.write_text(data, encoding="utf-8")
    else:
        path.write_bytes(data)
    return path


def survivors_of(paths: list[str], scope: Path, **kwargs: Any) -> list[str]:
    survivors, _ = plan(paths, scope, guard=TEST_GUARD, **kwargs)
    return [text for text, _ in survivors]


def skips_of(paths: list[str], scope: Path, **kwargs: Any) -> dict[str, str]:
    _, skipped = plan(paths, scope, guard=TEST_GUARD, **kwargs)
    return {record["path"]: record["reason"] for record in skipped}


def test_a_named_file_is_read_and_reported_under_its_own_name(tmp_path: Path) -> None:
    make(tmp_path / "notes.txt")
    assert survivors_of(["notes.txt", "./notes.txt"], tmp_path) == ["notes.txt"]


def test_a_directory_lists_immediate_files_and_recursive_walks_the_leaves(tmp_path: Path) -> None:
    make(tmp_path / "src/a.py")
    make(tmp_path / "src/deep/b.py")
    assert survivors_of(["src"], tmp_path) == ["src/a.py"]
    assert survivors_of(["src"], tmp_path, recursive=True) == ["src/a.py", "src/deep/b.py"]


def test_a_glob_matches_files_and_directories_are_walked_per_the_flag(tmp_path: Path) -> None:
    make(tmp_path / "src/a.py")
    make(tmp_path / "src/deep/b.py")
    make(tmp_path / "top.py")
    assert survivors_of(["*.py", "src/**/*.py"], tmp_path) == ["top.py", "src/a.py", "src/deep/b.py"]
    assert survivors_of(["src/**"], tmp_path) == ["src/a.py", "src/deep/b.py"]


def test_the_same_file_reached_twice_is_judged_once(tmp_path: Path) -> None:
    make(tmp_path / "src/a.py")
    survivors, skipped = plan(["src/a.py", "src", "src/*.py"], tmp_path, guard=TEST_GUARD)
    assert [text for text, _ in survivors] == ["src/a.py"]
    assert skipped == []


def test_skip_listed_directories_are_never_entered_and_a_named_file_inside_still_judges(
    tmp_path: Path,
) -> None:
    make(tmp_path / "node_modules/pkg/index.js")
    make(tmp_path / ".git/config")
    make(tmp_path / "src/keep.py")
    assert survivors_of([".", "src/keep.py"], tmp_path, recursive=True) == ["src/keep.py"]
    assert survivors_of(["node_modules/pkg/index.js"], tmp_path) == ["node_modules/pkg/index.js"]
    assert skips_of(["node_modules", ".git"], tmp_path) == {
        "node_modules": "skipped_directory",
        ".git": "skipped_directory",
    }


def test_a_symlink_to_outside_is_skipped_and_a_symlink_to_inside_judges(tmp_path: Path) -> None:
    inside = make(tmp_path / "in/real.txt")
    outside = make(tmp_path.parent / f"escape-{tmp_path.name}.txt")
    (tmp_path / "link-in").symlink_to(inside)
    (tmp_path / "link-out").symlink_to(outside)
    assert survivors_of(["link-in"], tmp_path) == ["link-in"]
    assert skips_of(["link-out"], tmp_path) == {"link-out": "outside_scope"}
    # The glob form of the same escape: one survivor, one skip, no provider call either way.
    assert survivors_of(["lin*"], tmp_path) == ["link-in"]
    assert skips_of(["lin*"], tmp_path) == {"link-out": "outside_scope"}


def test_a_dotdot_or_absolute_glob_refuses_before_expansion(tmp_path: Path) -> None:
    make(tmp_path / "x.txt")
    outside = make(tmp_path.parent / f"away-{tmp_path.name}.txt")
    assert skips_of(["../*"], tmp_path) == {"../*": "outside_scope"}
    assert skips_of([str(outside)], tmp_path) == {str(outside): "outside_scope"}


def test_the_prune_reasons_are_stable_and_the_order_is_expansion_then_read(tmp_path: Path) -> None:
    make(tmp_path / "b.bin", b"ok\x00 binary")
    make(tmp_path / "empty.txt", "")
    make(tmp_path / ".env", "TOKEN=deadbeef")
    make(tmp_path / "big.txt", "a" * (FILES_JUDGE.file_units_max + 1))
    skipped = skips_of(["missing.md", "*.txt", "*.bin", ".env", "nested"], tmp_path)
    assert skipped == {
        "missing.md": "not_found",
        "nested": "not_found",
        "empty.txt": "empty",
        "big.txt": "too_large",
        "b.bin": "binary",
        ".env": "secret_file",
    }


def test_the_hard_cap_skips_survivors_past_files_max_in_input_order(tmp_path: Path) -> None:
    for index in range(FILES_JUDGE.files_max + 2):
        make(tmp_path / f"f{index:02d}.txt")
    survivors, skipped = plan(["*.txt"], tmp_path, guard=TEST_GUARD)
    assert [text for text, _ in survivors] == [f"f{index:02d}.txt" for index in range(FILES_JUDGE.files_max)]
    # One aggregate row for the overflow — count plus the first few paths — not one row per file.
    assert skipped == [
        {
            "path": "+2 more files over the 64-file cap: f64.txt, f65.txt",
            "reason": "over_the_file_cap",
        }
    ]


def test_the_content_a_survivor_carries_is_the_redacted_text(tmp_path: Path) -> None:
    literal = "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ1234567890"
    make(tmp_path / "notes.txt", f"token: {literal}")
    survivors, _ = plan(["notes.txt"], tmp_path, guard=TEST_GUARD)
    _, content = survivors[0]
    assert literal not in content and "[redacted]" in content


def test_reading_stops_at_files_max_even_when_the_tree_is_huge(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The review bound: a 500-file tree yields at most files_max reads and one aggregate row."""
    make(tmp_path / "node_modules/pkg/index.js")
    for index in range(500):
        make(tmp_path / f"src/f{index:03d}.txt")
    calls = monkeypatch_read_counter(monkeypatch)
    survivors, skipped = plan(["."], tmp_path, recursive=True, guard=TEST_GUARD)
    assert len(survivors) == FILES_JUDGE.files_max
    assert calls() == FILES_JUDGE.files_max  # nothing past the cap is read
    assert skipped == [
        {
            "path": "+436 more files over the 64-file cap: src/f064.txt, src/f065.txt, src/f066.txt, …",
            "reason": "over_the_file_cap",
        }
    ]
    assert all("node_modules" not in row["path"] for row in skipped)


def test_a_glob_never_traverses_a_skip_listed_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Glob expansion prunes during the walk: node_modules is neither scanned nor listed."""
    for index in range(600):
        make(tmp_path / f"node_modules/pkg/f{index:03d}.js")
    make(tmp_path / "keep.py")
    reads = monkeypatch_read_counter(monkeypatch)
    survivors, skipped = plan(["**/*.py", "**/*.js"], tmp_path, guard=TEST_GUARD)
    assert [text for text, _ in survivors] == ["keep.py"]
    assert reads() == 1
    # The *.js glob matched nothing it may enter: not_found for the pattern, never a scan report.
    assert skipped == [{"path": "**/*.js", "reason": "not_found"}]


def test_discovery_stops_at_its_bound_and_reports_one_aggregate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for index in range(FILES_JUDGE.discovery_max + 40):
        make(tmp_path / f"f{index:04d}.txt")
    reads = monkeypatch_read_counter(monkeypatch)
    survivors, skipped = plan(["*"], tmp_path, guard=TEST_GUARD)
    assert len(survivors) == FILES_JUDGE.files_max
    assert reads() == FILES_JUDGE.files_max
    assert skipped[-1] == {
        "path": "discovery stopped at the 512-candidate bound; not expanded: f0512.txt, …",
        "reason": "over_the_file_cap",
    }
    assert skipped[0] == {
        "path": "+448 more files over the 64-file cap: f0064.txt, f0065.txt, f0066.txt, …",
        "reason": "over_the_file_cap",
    }


def test_skip_rows_collapse_into_one_aggregate_row_per_reason(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for index in range(FILES_JUDGE.skip_rows_max + 20):
        make(tmp_path / f"b{index:03d}.bin", b"ok\x00 binary")
    make(tmp_path / "good.txt")
    reads = monkeypatch_read_counter(monkeypatch)
    survivors, skipped = plan(["*.bin", "good.txt"], tmp_path, guard=TEST_GUARD)
    assert [text for text, _ in survivors] == ["good.txt"]
    individuals = [row for row in skipped if not row["path"].startswith("+")]
    aggregates = [row for row in skipped if row["path"].startswith("+")]
    assert len(individuals) == FILES_JUDGE.skip_rows_max
    assert aggregates == [
        {
            "path": "+20 more binary: b064.bin, b065.bin, b066.bin, …",
            "reason": "binary",
        }
    ]
    assert reads() == FILES_JUDGE.skip_rows_max + 21  # every candidate read: the cap never filled


def monkeypatch_read_counter(monkeypatch: pytest.MonkeyPatch) -> Any:
    """A counting spy on the module's read_state import; returns a zero-argument read total."""
    import jev_judge_mcp.tools.files_judge as module

    calls = {"n": 0}
    real = module.read_state

    def counting(path: Any, *args: Any, **kwargs: Any) -> str:
        calls["n"] += 1
        return real(path, *args, **kwargs)

    monkeypatch.setattr(module, "read_state", counting)
    return lambda: calls["n"]


class CountingProvider(FakeProvider):
    """Counts concurrent sends; a path containing `fail` fails its call, as a provider outage would."""

    def __init__(self, fail: str | None = None) -> None:
        super().__init__({})
        self.fail = fail
        self.in_flight = 0
        self.max_in_flight = 0

    @override
    async def _send(
        self, state: JsonValue, questions: dict[str, JsonValue], model: str, timeout: float | None
    ) -> Evaluation:
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        path = str(cast(dict[str, JsonValue], state).get("path"))
        try:
            await asyncio.sleep(0.01)
            if self.fail and self.fail in path:
                raise ProviderError("the provider is down")
            return Evaluation(dict(PERMISSIVE), Usage(1, 1), self.name, model)
        finally:
            self.in_flight -= 1


def batch_arguments(pattern: str = "*.txt") -> dict[str, Any]:
    return {"paths": [pattern], "kind": "score", "instructions": "q", "criteria": ["low", "high"]}


async def call_batch(provider: FakeProvider) -> Any:
    toolset = Toolset(Runtime(Settings(), provider_factory=lambda _: provider), TOOLS)
    try:
        return await toolset.call("jev_files_judge", batch_arguments())
    finally:
        await toolset.aclose()


@pytest.mark.anyio
async def test_the_fan_out_is_bounded_by_the_inflight_cap(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for index in range(6):
        make(tmp_path / f"f{index}.txt")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("JEV_MCP_MAX_INFLIGHT", "2")
    provider = CountingProvider()
    result = await call_batch(provider)
    assert not result.is_error
    assert provider.max_in_flight == 2


@pytest.mark.anyio
async def test_the_default_fan_out_caps_twenty_provider_calls(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for index in range(20):
        make(tmp_path / f"f{index}.txt")
    monkeypatch.chdir(tmp_path)
    provider = CountingProvider()
    result = await call_batch(provider)
    assert not result.is_error
    assert provider.max_in_flight == 8


@pytest.mark.anyio
@pytest.mark.parametrize("all_fail", [False, True], ids=["partial", "all-fail"])
async def test_a_per_file_failure_is_a_skip_that_keeps_the_batch_and_the_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, all_fail: bool
) -> None:
    make(tmp_path / "a_fails.txt")
    make(tmp_path / "b_ok.txt")
    make(tmp_path / "c_ok.txt")
    monkeypatch.chdir(tmp_path)
    provider = CountingProvider(fail=".txt" if all_fail else "a_fails")
    result = await call_batch(provider)
    assert not result.is_error
    payload = json.loads(result.content[0].text)
    assert payload["provider"] == "compatible"
    if all_fail:
        assert payload["results"] == []
        assert payload["skipped"] == [
            {"path": path, "reason": "call_failed:provider"} for path in ("a_fails.txt", "b_ok.txt", "c_ok.txt")
        ]
        assert payload["calls"] == 3
        assert payload["usage"] is None
        return
    assert [row["path"] for row in payload["results"]] == ["b_ok.txt", "c_ok.txt"]
    assert payload["skipped"] == [{"path": "a_fails.txt", "reason": "call_failed:provider"}]
    assert payload["calls"] == 3
    assert payload["usage"] == {"input_tokens": 2, "output_tokens": 2}


@pytest.mark.anyio
async def test_an_unconfigured_provider_is_the_auth_refusal_not_a_batch_of_skips(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    make(tmp_path / "a.txt")
    monkeypatch.chdir(tmp_path)

    def broken(settings: Settings) -> FakeProvider:
        raise ProviderConfigError("no credentials")

    toolset = Toolset(Runtime(Settings(), provider_factory=broken), TOOLS)
    try:
        result = await toolset.call("jev_files_judge", batch_arguments())
    finally:
        await toolset.aclose()
    assert result.is_error
    assert json.loads(cast(TextContent, result.content[-1]).text) == {"code": "auth"}


def test_scout_030_advertises_numeric_batch_limit() -> None:
    toolset = Toolset(Runtime(Settings()), TOOLS)
    definition = next(tool for tool in toolset.definitions() if tool.name == "jev_files_judge")
    assert f"of {FILES_JUDGE.files_max} surviving files" in (definition.description or "")


def test_scout_015_limits_nonmatching_directory_entries(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from dataclasses import replace

    from jev_judge_mcp.tools import files_judge

    monkeypatch.setattr(files_judge, "FILES_JUDGE", replace(FILES_JUDGE, discovery_max=2))
    for i in range(20):
        (tmp_path / f"file{i}").touch()
    kept, skipped = plan(["*.py"], scope=tmp_path, guard=TEST_GUARD)
    assert not kept
    assert any(row["reason"] == "over_the_file_cap" for row in skipped)


@pytest.mark.anyio
async def test_scout_015_planning_leaves_loop_responsive(monkeypatch: pytest.MonkeyPatch) -> None:
    import threading

    from jev_judge_mcp.tools import files_judge

    loop = asyncio.get_running_loop()
    entered = asyncio.Event()
    release = threading.Event()

    def hold(*args: object, **kwargs: object) -> tuple[list[tuple[str, str]], list[dict[str, str]]]:
        loop.call_soon_threadsafe(entered.set)
        assert release.wait(5), "planner blocked its caller's loop"
        return [], []

    monkeypatch.setattr(files_judge, "plan", hold)
    toolset = Toolset(Runtime(Settings(), provider_factory=lambda _: FakeProvider({})), TOOLS)
    try:
        task = asyncio.create_task(toolset.call("jev_files_judge", batch_arguments()))
        await asyncio.wait_for(entered.wait(), 5)
        release.set()
        assert not (await task).is_error
    finally:
        release.set()
        await toolset.aclose()


def test_scout_015_counts_scans_of_empty_directories(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from dataclasses import replace

    from jev_judge_mcp.tools import files_judge

    monkeypatch.setattr(files_judge, "FILES_JUDGE", replace(FILES_JUDGE, discovery_max=2))
    kept, skipped = plan(["**/**/**/**/**/notes.txt"], scope=tmp_path, guard=TEST_GUARD)
    assert not kept
    assert [row["reason"] for row in skipped] == ["over_the_file_cap"]


def test_scout_015_glob_walks_ordinary_in_scope_directory_link(tmp_path: Path) -> None:
    make(tmp_path / "docs/notes.txt")
    (tmp_path / "linked").symlink_to(tmp_path / "docs", target_is_directory=True)
    assert plan(["link*"], scope=tmp_path, guard=TEST_GUARD) == ([("linked/notes.txt", "content")], [])


def test_scout_015_reports_unreadable_directories(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from unittest.mock import Mock

    from jev_judge_mcp.tools import files_judge

    monkeypatch.setattr(files_judge, "pinned_directory", Mock(side_effect=PermissionError))
    kept, skipped = plan(["*.txt"], scope=tmp_path, guard=TEST_GUARD)
    assert kept == [] and skipped == [{"path": ".", "reason": "unreadable_directory"}]


def test_scout_015_worker_checks_cancellation_before_reading(tmp_path: Path) -> None:
    def cancel() -> None:
        raise asyncio.CancelledError

    make(tmp_path / "sample.txt")
    with pytest.raises(asyncio.CancelledError):
        plan(["*.txt"], scope=tmp_path, check_cancelled=cancel, guard=TEST_GUARD)


def test_scout_015_discovery_diagnostics_use_only_counts_and_duration(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    import logging

    make(tmp_path / "fixture-name.txt")
    with caplog.at_level(logging.DEBUG, logger="jev_judge_mcp.tools.files_judge"):
        plan(["*.txt"], scope=tmp_path, guard=TEST_GUARD)
    records = [record.getMessage() for record in caplog.records if record.name == "jev_judge_mcp.tools.files_judge"]
    assert len(records) == 1 and "examined=2" in records[0] and "truncated=0" in records[0]
    assert "duration_ms=" in records[0] and "fixture-name" not in records[0]


def test_literal_and_glob_follow_the_same_named_directory_alias(tmp_path: Path) -> None:
    make(tmp_path / "real/a.py")
    (tmp_path / "alias").symlink_to("real")
    assert plan(["alias/*.py"], tmp_path, guard=TEST_GUARD) == plan(["alias/a.py"], tmp_path, guard=TEST_GUARD)


def test_recursive_glob_charges_each_directory_listing_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from dataclasses import replace

    from jev_judge_mcp.tools import files_judge

    monkeypatch.setattr(files_judge, "FILES_JUDGE", replace(FILES_JUDGE, discovery_max=3))
    for name in ("a.md", "b.py", "c.md", "d.md", "e.md"):
        make(tmp_path / name)
    assert plan(["**/*.py"], tmp_path, guard=TEST_GUARD) == ([("b.py", "content")], [])
