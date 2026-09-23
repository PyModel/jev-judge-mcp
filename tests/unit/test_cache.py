"""The optional provider-response cache (ADR-0047): off by default, replay verbatim when on."""

import json
import os
import stat
from pathlib import Path

import pytest

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
