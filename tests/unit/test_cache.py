"""The optional provider-response cache (ADR-0047): off by default, replay verbatim when on."""

import asyncio
import json
import os
import stat
import threading
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import TypeVar

import pytest

from jev_judge_mcp import cache
from jev_judge_mcp.domain import JsonValue, Question, Usage
from jev_judge_mcp.providers import Evaluation, ProviderName
from jev_judge_mcp.settings import load_settings
from jev_judge_mcp.telemetry import Span
from jev_judge_mcp.tools import Runtime
from tests.support.jev import FakeProvider

pytestmark = pytest.mark.anyio

ANSWERS = {"grade": {"score": 0.5, "probabilities": {"0": 0.5, "1": 0.5}, "confidence": 0.9}}


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def cache_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    directory = tmp_path / "cache"
    monkeypatch.setenv("JEV_MCP_CACHE", "1")
    monkeypatch.setenv("JEV_MCP_CACHE_DIR", str(directory))
    return directory / "jev-records-v1"


def _question() -> dict[str, Question]:
    from jev_judge_mcp.domain import NoulCriteria, NoulQuestion

    return {"q": NoulQuestion("Is this fine?", NoulCriteria("yes", "no"))}


async def test_cache_is_off_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("JEV_MCP_CACHE", "JEV_MCP_CACHE_DIR"):
        monkeypatch.delenv(name, raising=False)
    provider = FakeProvider(ANSWERS)
    runtime = Runtime(load_settings(), provider_factory=lambda _: provider)
    await runtime.ask({"subject": "x"}, _question())
    await runtime.ask({"subject": "x"}, _question())
    assert len(provider.requests) == 2


async def test_a_repeat_request_replays_without_asking(cache_env: Path) -> None:
    provider = FakeProvider(ANSWERS)
    runtime = Runtime(load_settings(), provider_factory=lambda _: provider)
    first = await runtime.ask({"subject": "x"}, _question())
    second = await runtime.ask({"subject": "x"}, _question())
    assert len(provider.requests) == 1
    assert second == first
    assert (cache_env / "mcp.json").exists() is False  # exactly one entry, nothing else
    assert len(list(cache_env.iterdir())) == 1


async def test_a_changed_request_is_a_different_entry(cache_env: Path) -> None:
    provider = FakeProvider(ANSWERS)
    runtime = Runtime(load_settings(), provider_factory=lambda _: provider)
    await runtime.ask({"subject": "x"}, _question())
    await runtime.ask({"subject": "y"}, _question())
    assert len(provider.requests) == 2
    assert len(list(cache_env.iterdir())) == 2


async def test_a_changed_model_is_a_different_entry(cache_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    provider = FakeProvider(ANSWERS)
    runtime = Runtime(load_settings(), provider_factory=lambda _: provider)
    await runtime.ask({"subject": "x"}, _question())
    monkeypatch.setenv("JEV_MCP_MODEL", "jev-2")
    other = Runtime(load_settings(), provider_factory=lambda _: provider)
    await other.ask({"subject": "x"}, _question())
    assert len(provider.requests) == 2
    assert len(list(cache_env.iterdir())) == 2


async def test_a_corrupt_entry_is_a_miss(cache_env: Path) -> None:
    provider = FakeProvider(ANSWERS)
    runtime = Runtime(load_settings(), provider_factory=lambda _: provider)
    await runtime.ask({"subject": "x"}, _question())
    for entry in cache_env.iterdir():
        entry.write_text("{not json", encoding="utf-8")
    await runtime.ask({"subject": "x"}, _question())
    assert len(provider.requests) == 2


async def test_an_entry_from_another_provider_is_a_miss(cache_env: Path) -> None:
    provider = FakeProvider(ANSWERS)
    runtime = Runtime(load_settings(), provider_factory=lambda _: provider)
    await runtime.ask({"subject": "x"}, _question())
    for entry in cache_env.iterdir():
        record = json.loads(entry.read_text(encoding="utf-8"))
        record["provider"] = "typesafe"
        entry.write_text(json.dumps(record), encoding="utf-8")
    await runtime.ask({"subject": "x"}, _question())
    assert len(provider.requests) == 2


async def test_a_non_numeric_usage_entry_is_a_miss(cache_env: Path) -> None:
    provider = FakeProvider(ANSWERS)
    runtime = Runtime(load_settings(), provider_factory=lambda _: provider)
    await runtime.ask({"subject": "x"}, _question())
    for entry in cache_env.iterdir():
        record = json.loads(entry.read_text(encoding="utf-8"))
        record["usage"] = {"input_tokens": "many", "output_tokens": 1}
        entry.write_text(json.dumps(record), encoding="utf-8")
    await runtime.ask({"subject": "x"}, _question())
    assert len(provider.requests) == 2


async def test_an_unwritable_cache_directory_is_silent(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("JEV_MCP_CACHE", "1")
    monkeypatch.setenv("JEV_MCP_CACHE_DIR", str(tmp_path / "blocked"))
    (tmp_path / "blocked").write_text("a file, not a directory", encoding="utf-8")
    provider = FakeProvider(ANSWERS)
    runtime = Runtime(load_settings(), provider_factory=lambda _: provider)
    evaluation = await runtime.ask({"subject": "x"}, _question())
    assert isinstance(evaluation, Evaluation)
    await runtime.ask({"subject": "x"}, _question())
    assert len(provider.requests) == 2  # store failed, so the repeat asks again


def test_the_default_cache_directory_follows_xdg(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from jev_judge_mcp.cache import cache_dir

    monkeypatch.delenv("JEV_MCP_CACHE_DIR", raising=False)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    settings = load_settings()
    monkeypatch.delenv("XDG_CACHE_HOME")
    monkeypatch.setenv("HOME", str(tmp_path))
    assert cache_dir(settings) == tmp_path / "jev-mcp"
    assert cache_dir(load_settings()) == tmp_path / ".cache" / "jev-mcp"


async def test_a_non_object_entry_is_a_miss(cache_env: Path) -> None:
    provider = FakeProvider(ANSWERS)
    runtime = Runtime(load_settings(), provider_factory=lambda _: provider)
    await runtime.ask({"subject": "x"}, _question())
    for entry in cache_env.iterdir():
        entry.write_text("[1, 2]", encoding="utf-8")
    await runtime.ask({"subject": "x"}, _question())
    assert len(provider.requests) == 2


async def test_the_replayed_evaluation_is_verbatim(cache_env: Path) -> None:
    provider = FakeProvider(ANSWERS)
    runtime = Runtime(load_settings(), provider_factory=lambda _: provider)
    await runtime.ask({"subject": "x"}, _question())
    provider.answers = {"grade": {"score": 9, "probabilities": {}, "confidence": None}}
    replayed = await runtime.ask({"subject": "x"}, _question())
    assert replayed.answers == ANSWERS
    assert replayed.usage == Usage(1, 1)
    assert replayed.provider == "compatible"


async def test_entries_are_private(cache_env: Path) -> None:
    """A cache entry holds the judged State: mode 0600 inside a 0700 directory, like the key file."""
    provider = FakeProvider(ANSWERS)
    runtime = Runtime(load_settings(), provider_factory=lambda _: provider)
    await runtime.ask({"subject": "x"}, _question())
    entry = next(iter(cache_env.iterdir()))
    assert stat.S_IMODE(os.stat(entry).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(cache_env).st_mode) == 0o700


async def test_a_hit_spends_no_tokens_in_telemetry(cache_env: Path) -> None:
    """The hit span says `cache=hit` and records no token counts: nothing was billed for the replay."""
    provider = FakeProvider(ANSWERS)
    runtime = Runtime(load_settings(), provider_factory=lambda _: provider)
    await runtime.ask({"subject": "x"}, _question())
    await runtime.ask({"subject": "x"}, _question())
    evaluates = [s for s in runtime.telemetry.spans.spans if s.name == "jev.evaluate"]
    assert [s.attributes.get("cache") for s in evaluates] == [None, "hit"]
    assert "input_tokens" not in evaluates[1].attributes
    assert "output_tokens" not in evaluates[1].attributes
    assert evaluates[0].attributes["input_tokens"] == 1


async def test_an_entry_past_the_ttl_is_a_miss(cache_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A stale answer must not replay forever: past the TTL the entry is deleted and re-asked."""
    monkeypatch.setenv("JEV_MCP_CACHE_TTL_SECONDS", "60")
    provider = FakeProvider(ANSWERS)
    runtime = Runtime(load_settings(), provider_factory=lambda _: provider)
    await runtime.ask({"subject": "x"}, _question())
    stale = time.time() - 61
    os.utime(next(iter(cache_env.iterdir())), (stale, stale))
    settings = load_settings()
    assert cache.lookup(settings, "compatible", "", "jev-latest", {"subject": "x"}, _question()) is None
    assert list(cache_env.iterdir())  # a reader never unlinks a concurrent refresh
    await runtime.ask({"subject": "x"}, _question())
    assert len(provider.requests) == 2


async def test_a_zero_ttl_replays_aged_entries(cache_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JEV_MCP_CACHE_TTL_SECONDS", "0")
    provider = FakeProvider(ANSWERS)
    runtime = Runtime(load_settings(), provider_factory=lambda _: provider)
    await runtime.ask({"subject": "x"}, _question())
    aged = time.time() - 999_999
    os.utime(next(iter(cache_env.iterdir())), (aged, aged))
    await runtime.ask({"subject": "x"}, _question())
    assert len(provider.requests) == 1


async def test_the_entry_cap_evicts_the_oldest_first(cache_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JEV_MCP_CACHE_MAX_ENTRIES", "2")
    provider = FakeProvider(ANSWERS)
    runtime = Runtime(load_settings(), provider_factory=lambda _: provider)
    await runtime.ask({"subject": "a"}, _question())
    aged = time.time() - 300
    for entry in cache_env.iterdir():
        os.utime(entry, (aged, aged))
    await runtime.ask({"subject": "b"}, _question())  # fresh mtime
    await runtime.ask({"subject": "c"}, _question())  # a third entry evicts "a", the oldest
    assert len(list(cache_env.iterdir())) == 2
    await runtime.ask({"subject": "b"}, _question())  # kept: replays
    assert len(provider.requests) == 3
    await runtime.ask({"subject": "a"}, _question())  # evicted: asks again
    assert len(provider.requests) == 4


async def test_a_zero_cap_never_evicts(cache_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JEV_MCP_CACHE_MAX_ENTRIES", "0")
    provider = FakeProvider(ANSWERS)
    runtime = Runtime(load_settings(), provider_factory=lambda _: provider)
    for subject in ("a", "b", "c"):
        await runtime.ask({"subject": subject}, _question())
    assert len(list(cache_env.iterdir())) == 3


async def test_cache_key_and_file_io_run_off_the_event_loop(cache_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The whole-state key serialization and the file read/write never block the loop."""

    from jev_judge_mcp import cache as cache_module

    real_lookup, real_store = cache_module.lookup, cache_module.store
    threads: list[int] = []
    T = TypeVar("T")

    def in_worker(call: Callable[..., T]) -> Callable[..., T]:
        def wrapped(*args: object, **kwargs: object) -> T:
            threads.append(threading.get_ident())
            return call(*args, **kwargs)

        return wrapped

    monkeypatch.setattr(cache_module, "lookup", in_worker(real_lookup))
    monkeypatch.setattr(cache_module, "store", in_worker(real_store))
    provider = FakeProvider(ANSWERS)
    runtime = Runtime(load_settings(), provider_factory=lambda _: provider)
    await runtime.ask({"subject": "x"}, _question())
    await runtime.ask({"subject": "x"}, _question())
    assert len(provider.requests) == 1  # still a verbatim hit through the off-loop path
    assert threads and all(ident != threading.get_ident() for ident in threads)


@pytest.mark.parametrize(
    "max_entries",
    [pytest.param(None, id="default-cap"), pytest.param("0", id="no-evict")],
)
async def test_a_crashed_atomic_write_leftover_is_swept_on_the_next_store(
    cache_env: Path, monkeypatch: pytest.MonkeyPatch, max_entries: str | None
) -> None:
    """A SIGKILL between mkstemp and os.replace leaves a staging file; the next store unlinks it.

    Also in no-evict mode (`JEV_MCP_CACHE_MAX_ENTRIES=0`): the sweep is not eviction — dead files
    go in every mode, while live entries stay (ADR-0047's "swept by the next store" is not
    qualified by the cap).
    """
    if max_entries is not None:
        monkeypatch.setenv("JEV_MCP_CACHE_MAX_ENTRIES", max_entries)
    provider = FakeProvider(ANSWERS)
    runtime = Runtime(load_settings(), provider_factory=lambda _: provider)
    await runtime.ask({"subject": "x"}, _question())
    entry = next(iter(cache_env.iterdir()))
    staging = cache_env / f".{entry.name}.ab12cd34"
    staging.write_text("partial", encoding="utf-8")
    os.utime(staging, (1, 1))
    await runtime.ask({"subject": "y"}, _question())
    names = [path.name for path in cache_env.iterdir()]
    assert len(names) == 2  # two live entries; the staging file is gone
    assert all(name.endswith(".json") for name in names)


async def test_a_non_finite_usage_entry_is_a_miss(cache_env: Path) -> None:
    """`json.dumps` writes NaN and Infinity, `JSON.parse` never reads them: a record carrying either
    is corrupt by the envelope's own usage rule and must not replay."""
    provider = FakeProvider(ANSWERS)
    runtime = Runtime(load_settings(), provider_factory=lambda _: provider)
    await runtime.ask({"subject": "x"}, _question())
    for entry in cache_env.iterdir():
        record = json.loads(entry.read_text(encoding="utf-8"))
        record["usage"] = {"input_tokens": float("nan"), "output_tokens": float("inf")}
        entry.write_text(json.dumps(record), encoding="utf-8")
    await runtime.ask({"subject": "x"}, _question())
    assert len(provider.requests) == 2


async def test_two_endpoints_under_one_provider_name_never_replay_each_other(cache_env: Path) -> None:
    """The key names the endpoint (ADR-0047 amendment): a staging and a production base URL under
    the same provider name are different entries, so the second asks instead of replaying."""
    staging = FakeProvider(ANSWERS)
    staging.cache_scope = "https://staging.example/v1"
    production = FakeProvider(ANSWERS)
    production.cache_scope = "https://api.example/v1"
    await Runtime(load_settings(), provider_factory=lambda _: staging).ask({"subject": "x"}, _question())
    await Runtime(load_settings(), provider_factory=lambda _: production).ask({"subject": "x"}, _question())
    assert len(staging.requests) == 1 and len(production.requests) == 1
    assert len(list(cache_env.iterdir())) == 2
    same = FakeProvider(ANSWERS)
    same.cache_scope = "https://api.example/v1"
    await Runtime(load_settings(), provider_factory=lambda _: same).ask({"subject": "x"}, _question())
    assert same.requests == []  # the same endpoint replays


@pytest.mark.parametrize("cap", [0, 1])
def test_scout_001_preserves_unowned_files_and_fresh_staging(tmp_path: Path, cap: int) -> None:
    sentinels = ["other.json", ".other.json.notes", "." + "a" * 64 + ".json.abc12345"]
    for name in sentinels:
        (tmp_path / name).touch()
    for digit in "01":
        (tmp_path / (digit * 64 + ".json")).touch()
    cache._evict(tmp_path, cap)  # pyright: ignore[reportPrivateUsage]
    assert all((tmp_path / name).exists() for name in sentinels)
    assert (tmp_path / ("1" * 64 + ".json")).exists()


async def test_scout_025_request_id_survives_replay(cache_env: Path) -> None:
    from jev_judge_mcp.tools.base import frame

    provider = FakeProvider(ANSWERS, request_id="trace-42")
    runtime = Runtime(load_settings(), provider_factory=lambda _: provider)
    first = await runtime.ask({}, _question())
    assert frame("tool", first, {}) == frame("tool", await runtime.ask({}, _question()), {})
    assert len(provider.requests) == 1


def _observe_follower(monkeypatch: pytest.MonkeyPatch) -> asyncio.Event:
    from jev_judge_mcp.tools.base import _Pending  # pyright: ignore[reportPrivateUsage]

    joined = asyncio.Event()
    original = _Pending.replay

    async def wait(pending: _Pending, span: Span) -> Evaluation:
        if pending.subscribers:
            joined.set()
        return await original(pending, span)

    monkeypatch.setattr(_Pending, "replay", wait)
    return joined


@pytest.mark.parametrize("cap", [0, 1])
async def test_scout_035_simultaneous_cache_misses(cache_env: Path, monkeypatch: pytest.MonkeyPatch, cap: int) -> None:
    from unittest.mock import AsyncMock

    monkeypatch.setenv("JEV_MCP_MAX_INFLIGHT", str(cap))
    provider = FakeProvider(ANSWERS)
    evaluate = provider.evaluate
    entered = asyncio.Event()
    release = asyncio.Event()

    async def hold(
        state: JsonValue, questions: Mapping[str, Question], model: str, timeout: float | None
    ) -> Evaluation:
        entered.set()
        await release.wait()
        return await evaluate(state, questions, model, timeout)

    monkeypatch.setattr(provider, "evaluate", AsyncMock(side_effect=hold))
    runtime = Runtime(load_settings(), provider_factory=lambda _: provider)
    joined = _observe_follower(monkeypatch)
    first = asyncio.create_task(runtime.ask({}, _question()))
    await entered.wait()
    second = asyncio.create_task(runtime.ask({}, _question()))
    await asyncio.wait_for(joined.wait(), 5)
    release.set()
    assert await first == await second
    assert len(provider.requests) == 1


def test_scout_036_no_cap_avoids_allocating_entry_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / ("0" * 64 + ".json")).touch()

    def disallow(*args: object) -> None:
        pytest.fail("enumerated Path objects for a disabled entry cap")

    monkeypatch.setattr(Path, "iterdir", disallow)
    cache._evict(tmp_path, 0)  # pyright: ignore[reportPrivateUsage]


@pytest.mark.parametrize("cap", [0, 1])
async def test_scout_001_never_sweeps_legacy_directory(
    cache_env: Path, monkeypatch: pytest.MonkeyPatch, cap: int
) -> None:
    monkeypatch.setenv("JEV_MCP_CACHE_MAX_ENTRIES", str(cap))
    cache_env.parent.mkdir(mode=0o700)
    legacy = cache_env.parent / ("0" * 64 + ".json")
    legacy.write_text("independently owned")
    os.utime(legacy, (1, 1))
    runtime = Runtime(load_settings(), provider_factory=lambda _: FakeProvider(ANSWERS))
    for name in ("first", "second"):
        await runtime.ask({"name": name}, _question())
    assert legacy.read_text() == "independently owned"
    assert len(list(cache_env.iterdir())) == (1 if cap else 2)


@pytest.mark.parametrize("identifier,expected_calls", [(None, 1), (42, 2), ("x" * 1025, 2), ("bad\ntrace", 2)])
async def test_cached_request_ids_are_validated(cache_env: Path, identifier: object, expected_calls: int) -> None:
    provider = FakeProvider(ANSWERS)
    runtime = Runtime(load_settings(), provider_factory=lambda _: provider)
    await runtime.ask({}, _question())
    entry = next(cache_env.iterdir())
    record = json.loads(entry.read_bytes())
    record.pop("request_id")
    entry.write_text(json.dumps(record))
    assert (await runtime.ask({}, _question())).request_id is None
    assert len(provider.requests) == 1
    record["request_id"] = identifier
    entry.write_text(json.dumps(record))
    assert (await runtime.ask({}, _question())).request_id is None
    assert len(provider.requests) == expected_calls


async def test_records_in_the_cache_root_are_ignored(cache_env: Path) -> None:
    provider = FakeProvider(ANSWERS)
    runtime = Runtime(load_settings(), provider_factory=lambda _: provider)
    await runtime.ask({}, _question())
    entry = next(cache_env.iterdir())
    entry.rename(cache_env.parent / entry.name)
    await runtime.ask({}, _question())
    assert len(provider.requests) == 2


@pytest.mark.parametrize(
    ("provider", "recorded_model", "expected"),
    [
        ("compatible", "x" * 1025, None),
        ("cloudflare", "bad\nmodel", None),
        ("openrouter", "bad\nmodel", None),
        ("cloudflare", "provider-version", "provider-version"),
    ],
    ids=["oversized", "cloudflare-control", "openrouter-control", "valid"],
)
def test_legacy_upstream_model_is_bounded_on_replay(
    cache_env: Path, provider: ProviderName, recorded_model: str, expected: str | None
) -> None:
    settings = load_settings()
    questions = _question()
    evaluation = Evaluation({}, Usage(1, 1), provider, recorded_model)
    cache.store(settings, provider, "", "jev-latest", {}, questions, evaluation)

    replayed = cache.lookup(settings, provider, "", "jev-latest", {}, questions)

    if expected is None:
        assert replayed is None
    else:
        assert replayed is not None
        assert replayed.model == expected


async def test_scout_035_each_call_serializes_its_identity_once(
    cache_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from unittest.mock import Mock

    count = Mock(wraps=cache.request_key)
    monkeypatch.setattr(cache, "request_key", count)
    runtime = Runtime(load_settings(), provider_factory=lambda _: FakeProvider(ANSWERS))
    for expected in (1, 2):
        await runtime.ask({}, _question())
        assert count.call_count == expected


async def test_oversized_cache_record_is_a_miss(cache_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    provider = FakeProvider(ANSWERS)
    runtime = Runtime(load_settings(), provider_factory=lambda _: provider)
    await runtime.ask({}, _question())
    entry = next(cache_env.iterdir())
    size = entry.stat().st_size
    monkeypatch.setattr(cache, "_RECORD_BYTES_MAX", size)
    with entry.open("ab") as handle:
        handle.write(b" ")
    await runtime.ask({}, _question())
    assert len(provider.requests) == 2


@pytest.mark.parametrize("outcome", ["follower_cancel", "leader_failure", "leader_cancel"])
@pytest.mark.parametrize("cap", [0, 1])
async def test_scout_035_cancel_and_failure_release_waiters(
    cache_env: Path, monkeypatch: pytest.MonkeyPatch, outcome: str, cap: int
) -> None:
    from unittest.mock import AsyncMock

    monkeypatch.setenv("JEV_MCP_MAX_INFLIGHT", str(cap))
    provider = FakeProvider(ANSWERS)
    entered = asyncio.Event()
    release = asyncio.Event()
    joined = _observe_follower(monkeypatch)
    original = provider.evaluate

    async def hold(
        state: JsonValue, questions: Mapping[str, Question], model: str, timeout: float | None
    ) -> Evaluation:
        if not entered.is_set():
            entered.set()
            await release.wait()
            if outcome == "leader_failure":
                raise RuntimeError("fixture")
        return await original(state, questions, model, timeout)

    evaluate = AsyncMock(side_effect=hold)
    monkeypatch.setattr(provider, "evaluate", evaluate)
    runtime = Runtime(load_settings(), provider_factory=lambda _: provider)
    async with asyncio.timeout(5):
        leader = asyncio.create_task(runtime.ask({}, _question()))
        await entered.wait()
        follower = asyncio.create_task(runtime.ask({}, _question()))
        await joined.wait()
        if outcome == "follower_cancel":
            follower.cancel()
            with pytest.raises(asyncio.CancelledError):
                await follower
            release.set()
            assert await leader == await runtime.ask({}, _question())
            assert evaluate.await_count == 1
        elif outcome == "leader_cancel":
            leader.cancel()
            with pytest.raises(asyncio.CancelledError):
                await leader
            release.set()
            assert await follower == await runtime.ask({}, _question())
            assert evaluate.await_count == 1
            # The provider call's usage is counted once, on the caller still waiting for it.
            assert runtime.telemetry.metrics.snapshot()['tokens{direction="input"}'] == 1
        else:
            release.set()
            for task in (leader, follower):
                with pytest.raises(RuntimeError):
                    await task
            await runtime.ask({}, _question())
            assert evaluate.await_count == 2


def test_scout_036_retries_a_failed_eviction_on_next_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    import logging

    caplog.set_level(logging.DEBUG, logger="jev_judge_mcp.cache")
    from unittest.mock import Mock

    for i in range(4):
        entry = tmp_path / f"{i:064x}.json"
        entry.touch()
        os.utime(entry, (i + 1, i + 1))
    import heapq

    choose = Mock(wraps=heapq.nsmallest)
    monkeypatch.setattr(heapq, "nsmallest", choose)
    with monkeypatch.context() as local:
        local.setattr(os, "unlink", Mock(side_effect=PermissionError))
        cache._evict(tmp_path, 3)  # pyright: ignore[reportPrivateUsage]
        assert len(list(tmp_path.iterdir())) == 4
    cache._evict(tmp_path, 3)  # pyright: ignore[reportPrivateUsage]
    assert "evicted=0 failed=1" in caplog.text and "evicted=1 failed=0" in caplog.text
    assert [call.args[0] for call in choose.call_args_list] == [1, 1]
    assert sorted(entry.name for entry in tmp_path.iterdir()) == [f"{i:064x}.json" for i in (1, 2, 3)]


async def test_scout_001_expired_reader_leaves_replacement_intact(
    cache_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = load_settings()
    provider = FakeProvider(ANSWERS)
    runtime = Runtime(settings, provider_factory=lambda _: provider)
    await runtime.ask({}, _question())
    entry = next(cache_env.iterdir())
    replacement = entry.with_suffix(".fresh")
    replacement.write_bytes(entry.read_bytes())
    os.utime(entry, (1, 1))
    original = os.fstat

    def after_open(fd: int) -> os.stat_result:
        info = original(fd)
        replacement.replace(entry)
        return info

    with monkeypatch.context() as local:
        local.setattr(os, "fstat", after_open)
        assert cache.lookup(settings, provider.name, provider.cache_scope, runtime.model, {}, _question()) is None
    assert (await runtime.ask({}, _question())).answers == ANSWERS
    assert len(provider.requests) == 1


def test_cache_module_loads_before_the_posix_startup_check(monkeypatch: pytest.MonkeyPatch) -> None:
    import runpy
    import sys

    monkeypatch.setitem(sys.modules, "fcntl", None)
    assert "store" in runpy.run_path(cache.__file__)


async def test_busy_publication_delivers_answer_and_records_skip(
    cache_env: Path, caplog: pytest.LogCaptureFixture
) -> None:
    import fcntl
    import logging

    caplog.set_level(logging.DEBUG, logger="jev_judge_mcp.cache")
    cache_env.parent.mkdir(mode=0o700)
    cache_env.mkdir(mode=0o700)
    fd = os.open(cache_env, os.O_RDONLY)
    fcntl.flock(fd, fcntl.LOCK_EX)
    runtime = Runtime(load_settings(), provider_factory=lambda _: FakeProvider(ANSWERS))
    try:
        task = asyncio.create_task(runtime.ask({}, _question()))
        done, _ = await asyncio.wait([task], timeout=1)
    finally:
        os.close(fd)
    evaluation = await task
    assert task in done and evaluation.answers == ANSWERS
    assert not list(cache_env.iterdir())
    assert "busy" in caplog.text


@pytest.mark.parametrize("reported", [True, False])
def test_cache_replays_usage_provenance(cache_env: Path, reported: bool) -> None:
    from jev_judge_mcp.providers.base import parse_envelope

    body: dict[str, object] = {"answers": ANSWERS}
    if reported:
        body["usage"] = {"input_tokens": 0, "output_tokens": 0}
    envelope = parse_envelope(body, "fixture")
    usage = envelope.usage
    evaluation = Evaluation(answers=envelope.answers, usage=usage, provider="compatible", model="fixture")
    settings = load_settings()
    cache.store(settings, "compatible", "local", "fixture", {}, _question(), evaluation)
    record = json.loads(next(cache_env.iterdir()).read_text())
    assert record["usage"]["reported"] is reported
    replay = cache.lookup(settings, "compatible", "local", "fixture", {}, _question())
    assert replay is not None and replay.usage == usage


async def test_cache_waiting_for_slot_cannot_start_after_shutdown(
    cache_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import anyio

    provider = FakeProvider(ANSWERS)
    runtime = Runtime(load_settings(), provider_factory=lambda _: provider)
    slots = anyio.Semaphore(1)
    monkeypatch.setattr(runtime, "_cache_slots", slots)
    await slots.acquire()
    async with asyncio.timeout(5):
        caller = asyncio.create_task(runtime.ask({}, _question()))
        while not slots.statistics().tasks_waiting:
            await asyncio.sleep(0)
        await runtime.aclose()
        slots.release()
        with pytest.raises(asyncio.CancelledError):
            await caller
    assert not provider.requests


@pytest.mark.parametrize("count,expected", [(0, False), (7, True)])
def test_legacy_cache_metering_uses_available_evidence(cache_env: Path, count: int, expected: bool) -> None:
    settings = load_settings()
    evaluation = Evaluation(answers={}, usage=Usage(count, 0), provider="compatible", model="fixture")
    cache.store(settings, "compatible", "local", "fixture", {}, _question(), evaluation)
    entry = next(cache_env.iterdir())
    record = json.loads(entry.read_text())
    record["usage"].pop("reported", None)
    entry.write_text(json.dumps(record))
    replay = cache.lookup(settings, "compatible", "local", "fixture", {}, _question())
    assert replay is not None and replay.usage.reported is expected
    record["usage"]["reported"] = "unknown"
    entry.write_text(json.dumps(record))
    assert cache.lookup(settings, "compatible", "local", "fixture", {}, _question()) is None


@pytest.mark.parametrize("cap", [0, 1])
async def test_last_subscriber_leaving_after_dispatch_cancels_the_provider_call(
    cache_env: Path, monkeypatch: pytest.MonkeyPatch, cap: int
) -> None:
    monkeypatch.setenv("JEV_MCP_MAX_INFLIGHT", str(cap))
    provider = FakeProvider(ANSWERS)
    entered = asyncio.Event()
    cancelled = asyncio.Event()

    async def hold(
        state: JsonValue, questions: Mapping[str, Question], model: str, timeout: float | None
    ) -> Evaluation:
        entered.set()
        try:
            return await asyncio.get_running_loop().create_future()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    monkeypatch.setattr(provider, "evaluate", hold)
    runtime = Runtime(load_settings(), provider_factory=lambda _: provider)
    async with asyncio.timeout(5):
        caller = asyncio.create_task(runtime.ask({}, _question()))
        await entered.wait()
        caller.cancel()
        with pytest.raises(asyncio.CancelledError):
            await caller
        await cancelled.wait()
        while runtime._pending:  # pyright: ignore[reportPrivateUsage]
            await asyncio.sleep(0)


async def test_last_subscriber_leaving_before_dispatch_avoids_provider_call(
    cache_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import anyio

    monkeypatch.setenv("JEV_MCP_MAX_INFLIGHT", "1")
    provider = FakeProvider(ANSWERS)
    runtime = Runtime(load_settings(), provider_factory=lambda _: provider)
    slots = anyio.Semaphore(1)
    monkeypatch.setattr(runtime, "_inflight", slots)
    await slots.acquire()
    async with asyncio.timeout(5):
        caller = asyncio.create_task(runtime.ask({}, _question()))
        while not slots.statistics().tasks_waiting:
            await asyncio.sleep(0)
        caller.cancel()
        with pytest.raises(asyncio.CancelledError):
            await caller
        slots.release()
        while runtime._pending:  # pyright: ignore[reportPrivateUsage]
            await asyncio.sleep(0)
        assert not provider.requests
        await runtime.ask({}, _question())
        assert len(provider.requests) == 1
