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
from jev_judge_mcp.providers import Evaluation, ProviderConfigError
from jev_judge_mcp.settings import Settings
from jev_judge_mcp.tools import TOOLS, Runtime, Toolset
from jev_judge_mcp.tools.files_judge import plan
from tests.support.jev import FakeProvider

PERMISSIVE = {"file": {"score": 0.2, "probabilities": {"0": 0.8, "1": 0.2}, "confidence": 0.95}}


def make(path: Path, data: bytes | str = "content") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, str):
        path.write_text(data, encoding="utf-8")
    else:
        path.write_bytes(data)
    return path


def survivors_of(paths: list[str], scope: Path, **kwargs: Any) -> list[str]:
    survivors, _ = plan(paths, scope, **kwargs)
    return [text for text, _ in survivors]


def skips_of(paths: list[str], scope: Path, **kwargs: Any) -> dict[str, str]:
    _, skipped = plan(paths, scope, **kwargs)
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
    survivors, skipped = plan(["src/a.py", "src", "src/*.py"], tmp_path)
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
    survivors, skipped = plan(["*.txt"], tmp_path)
    assert [text for text, _ in survivors] == [f"f{index:02d}.txt" for index in range(FILES_JUDGE.files_max)]
    assert skipped == [
        {"path": "f64.txt", "reason": "over_the_file_cap"},
        {"path": "f65.txt", "reason": "over_the_file_cap"},
    ]


def test_the_content_a_survivor_carries_is_the_redacted_text(tmp_path: Path) -> None:
    literal = "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ1234567890"
    make(tmp_path / "notes.txt", f"token: {literal}")
    survivors, _ = plan(["notes.txt"], tmp_path)
    _, content = survivors[0]
    assert literal not in content and "[redacted]" in content


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
                raise RuntimeError("the provider is down")
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
async def test_without_a_cap_the_calls_overlap(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for index in range(4):
        make(tmp_path / f"f{index}.txt")
    monkeypatch.chdir(tmp_path)
    provider = CountingProvider()
    result = await call_batch(provider)
    assert not result.is_error
    assert provider.max_in_flight == 4


@pytest.mark.anyio
async def test_a_per_file_failure_is_a_skip_that_keeps_the_batch_and_the_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    make(tmp_path / "a_fails.txt")
    make(tmp_path / "b_ok.txt")
    make(tmp_path / "c_ok.txt")
    monkeypatch.chdir(tmp_path)
    provider = CountingProvider(fail="a_fails")
    result = await call_batch(provider)
    assert not result.is_error
    payload = json.loads(result.content[0].text)
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
