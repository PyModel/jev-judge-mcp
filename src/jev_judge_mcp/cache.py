"""The optional provider-response cache (ADR-0047): off unless `JEV_MCP_CACHE` is truthy.

An identical request — same provider, model, state, and questions — replays the recorded
`Evaluation` from a JSON file under the cache directory instead of calling the provider again, at
zero API cost. Replay is verbatim, so every tool payload built from it is byte-identical to the
first answer's; the cache never edits a response. Keep it off when decisions must stay fresh.

Entries expire after `JEV_MCP_CACHE_TTL_SECONDS` and the directory holds at most
`JEV_MCP_CACHE_MAX_ENTRIES` entries (oldest evicted first); with the cache off, nothing reads or
writes the directory. The key serialization and the file IO run in a worker thread, so a large
state cannot stall the event loop (ADR-0047 amendment).

The key is the SHA-256 of the exact request body (`JSON.stringify` semantics, key order included),
so any difference — model slug, one character of state, a reordered question map — is a different
entry. Entries are mode 0600 inside a 0700 directory (`fsutil`): they hold the judged State. Writes
are atomic; a cache that cannot be read or written is a miss, never an error.
"""

import hashlib
import json
import logging
import time
from collections.abc import Mapping
from pathlib import Path
from typing import cast

import anyio

from jev_judge_mcp import fsutil
from jev_judge_mcp.domain import JsonValue, Question, Usage, questions_to_wire
from jev_judge_mcp.providers.base import Evaluation, ProviderName
from jev_judge_mcp.serialize import stringify_compact
from jev_judge_mcp.settings import Settings

logger = logging.getLogger("jev_judge_mcp.cache")

_INVALID_COUNTS: tuple[float, float] = (-1.0, -1.0)


def _usage_counts(usage: Mapping[str, object]) -> tuple[float, float]:
    """Both counts as numbers, or the `_INVALID_COUNTS` marker (bool is not a number)."""
    counts: list[float] = []
    for key in ("input_tokens", "output_tokens"):
        value = usage.get(key)
        if type(value) not in (int, float):
            return _INVALID_COUNTS
        counts.append(value)  # type: ignore[reportArgumentType] -- narrowed to a number above
    return counts[0], counts[1]


def _enabled(settings: Settings) -> bool:
    return settings.jev_judge_mcp_cache


def cache_dir(settings: Settings) -> Path:
    """`JEV_MCP_CACHE_DIR`, else the XDG cache default."""
    if settings.cache_dir is not None:
        return settings.cache_dir
    return fsutil.xdg_home("cache") / "jev-mcp"


def _key(provider: ProviderName, model: str, state: JsonValue, questions: Mapping[str, Question]) -> str:
    body = stringify_compact(
        {"provider": provider, "model": model, "state": state, "questions": questions_to_wire(questions)}
    )
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _stale(path: Path, ttl_seconds: float, *, now: float) -> bool:
    """True when the entry is older than the TTL (ADR-0047 amendment). `0` never expires."""
    if ttl_seconds <= 0:
        return False
    try:
        return now - path.stat().st_mtime > ttl_seconds
    except OSError:
        return False


def _evict(directory: Path, cap: int) -> None:
    """Keep at most `cap` entries, oldest mtime first (ADR-0047 amendment). `0` never evicts.

    A crashed atomic write leaves its mkstemp staging file (`.<name>.<random>`) behind; those are
    dead on arrival and are unlinked first, before the cap counts live entries. A staging file of
    a concurrent store can be caught by the same sweep: that store's `os.replace` then fails,
    which means "not cached", the same silence as an unwritable directory. Only this cache's
    `.json` entries and its staging files are touched; a directory that cannot be listed or an
    entry that cannot be deleted is ignored, never an error.
    """
    if cap <= 0:
        return
    try:
        entries = list(directory.iterdir())
    except OSError:
        return
    live: list[Path] = []
    for entry in entries:
        if entry.suffix == ".json":
            live.append(entry)
        elif entry.name.startswith(".") and ".json." in entry.name:
            try:
                entry.unlink()
            except OSError:
                pass
    excess = len(live) - cap
    if excess <= 0:
        return

    def stamp(entry: Path) -> tuple[float, str]:
        try:
            return (entry.stat().st_mtime, entry.name)
        except OSError:
            return (0.0, entry.name)

    for entry in sorted(live, key=stamp)[:excess]:
        try:
            entry.unlink()
        except OSError:
            pass


def lookup(
    settings: Settings, provider: ProviderName, model: str, state: JsonValue, questions: Mapping[str, Question]
) -> Evaluation | None:
    """The recorded evaluation for this exact request, or `None` (also when the cache is off)."""
    if not _enabled(settings):
        return None
    path = cache_dir(settings) / f"{_key(provider, model, state, questions)}.json"
    if _stale(path, settings.cache_ttl_seconds, now=time.time()):
        try:
            path.unlink()
        except OSError:
            pass
        return None
    try:
        parsed: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(parsed, dict):
        return None
    record = cast(dict[str, object], parsed)
    answers = record.get("answers")
    usage = record.get("usage")
    name = record.get("provider")
    recorded_model = record.get("model")
    if not isinstance(answers, dict) or not isinstance(usage, dict) or name != provider:
        return None
    counts = Usage(*_usage_counts(cast(dict[str, object], usage)))
    if counts.input_tokens < 0 or counts.output_tokens < 0:
        return None
    logger.info("cache hit for %s model %s", provider, recorded_model)
    return Evaluation(
        answers=cast(dict[str, object], answers),
        usage=counts,
        provider=provider,
        model=recorded_model if isinstance(recorded_model, str) else model,
    )


def store(
    settings: Settings,
    provider: ProviderName,
    model: str,
    state: JsonValue,
    questions: Mapping[str, Question],
    evaluation: Evaluation,
) -> None:
    """Record `evaluation` for this request. Off, or unwritable, means simply: not cached.

    Entries are mode 0600 inside a 0700 directory (`fsutil`): a cache file holds the judged State,
    so it is at least as protected as the stored API key.
    """
    if not _enabled(settings):
        return
    directory = cache_dir(settings)
    digest = _key(provider, model, state, questions)
    record = {
        "answers": evaluation.answers,
        "usage": {"input_tokens": evaluation.usage.input_tokens, "output_tokens": evaluation.usage.output_tokens},
        "provider": evaluation.provider,
        "model": evaluation.model,
    }
    try:
        text = json.dumps(record, ensure_ascii=False)
        fsutil.write_private_atomic(directory / f"{digest}.json", text)
    except (OSError, TypeError, ValueError):
        return
    _evict(directory, settings.cache_max_entries)


async def alookup(
    settings: Settings, provider: ProviderName, model: str, state: JsonValue, questions: Mapping[str, Question]
) -> Evaluation | None:
    """`lookup` off the event loop: the key serializes the whole state, so neither the hash nor
    the file read stalls concurrent calls while the cache is on (ADR-0047 amendment).

    With the cache off (the default) this returns before the thread hop: the off path schedules
    nothing, exactly as before the bounds.
    """
    if not _enabled(settings):
        return None
    return await anyio.to_thread.run_sync(lambda: lookup(settings, provider, model, state, questions))


async def astore(
    settings: Settings,
    provider: ProviderName,
    model: str,
    state: JsonValue,
    questions: Mapping[str, Question],
    evaluation: Evaluation,
) -> None:
    """`store` off the event loop, for the same reason as `alookup` (ADR-0047 amendment)."""
    if not _enabled(settings):
        return
    await anyio.to_thread.run_sync(lambda: store(settings, provider, model, state, questions, evaluation))
