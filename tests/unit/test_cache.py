"""The optional provider-response cache (ADR-0047): off by default, replay verbatim when on."""

import json
import os
import stat
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

import pytest

from jev_judge_mcp import cache
from jev_judge_mcp.domain import Question, Usage
from jev_judge_mcp.providers import Evaluation
from jev_judge_mcp.settings import load_settings
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
    return directory


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
    assert cache_dir(load_settings()) == tmp_path / "jev-mcp"
    monkeypatch.delenv("XDG_CACHE_HOME")
    monkeypatch.setenv("HOME", str(tmp_path))
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
    assert cache.lookup(settings, "compatible", "jev-latest", {"subject": "x"}, _question()) is None
    assert not list(cache_env.iterdir())  # the stale entry was removed, not just skipped
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
    (cache_env / f".{entry.name}.ab12").write_text("partial", encoding="utf-8")
    await runtime.ask({"subject": "y"}, _question())
    names = [path.name for path in cache_env.iterdir()]
    assert len(names) == 2  # two live entries; the staging file is gone
    assert all(name.endswith(".json") for name in names)
