"""The optional provider-response cache (ADR-0047): off unless `JEV_MCP_CACHE` is truthy.

An identical request — same provider, endpoint, model, state, and questions — replays the recorded
`Evaluation` from a JSON file under the cache directory instead of calling the provider again, at
zero API cost. Replay is verbatim, so every tool payload built from it is byte-identical to the
first answer's; the cache never edits a response. Keep it off when decisions must stay fresh.

Entries expire after `JEV_MCP_CACHE_TTL_SECONDS` and the directory holds at most
`JEV_MCP_CACHE_MAX_ENTRIES` entries (oldest evicted first); with the cache off, nothing reads or
writes the directory. The key serialization and the file IO run in a worker thread, so a large
state cannot stall the event loop (ADR-0047 amendment).

The key is the SHA-256 of the exact request body (`JSON.stringify` semantics, key order included),
so any difference — model slug, one character of state, a reordered question map — is a different
entry. Entries are mode 0600 inside a 0700 directory (`fsutil`): they hold provider answers and usage. Writes
are atomic; a cache that cannot be read or written is a miss, never an error.
"""

import contextlib
import hashlib
import heapq
import json
import logging
import os
import re
import stat
import time
from collections.abc import Mapping
from pathlib import Path
from typing import cast

import anyio

from jev_judge_mcp import fsutil
from jev_judge_mcp.domain import JsonValue, Question, Usage, questions_to_wire
from jev_judge_mcp.domain.json import decode_json
from jev_judge_mcp.providers.base import Evaluation, ProviderName, usage_counts
from jev_judge_mcp.serialize import stringify_compact
from jev_judge_mcp.settings import Settings

logger = logging.getLogger("jev_judge_mcp.cache")


def _enabled(settings: Settings) -> bool:
    return settings.jev_judge_mcp_cache


def cache_dir(settings: Settings) -> Path:
    """`JEV_MCP_CACHE_DIR`, else the XDG cache default."""
    if settings.cache_dir is not None:
        return settings.cache_dir
    return fsutil.xdg_home("cache") / "jev-mcp"


def request_key(
    provider: ProviderName, scope: str, model: str, state: JsonValue, questions: Mapping[str, Question]
) -> str:
    body = stringify_compact(
        {
            "provider": provider,
            "endpoint": scope,
            "model": model,
            "state": state,
            "questions": questions_to_wire(questions),
        }
    )
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


_ENTRY = re.compile(r"[0-9a-f]{64}\.json")
_STAGE = re.compile(r"\.[0-9a-f]{64}\.json\.[a-z0-9_]{8}")
_STAGE_GRACE = 86_400
_REQUEST_ID_MAX = 1024
_RECORD_BYTES_MAX = 32 * 1024 * 1024
_RECORDS = "jev-records-v1"


def _evict(directory: Path, cap: int) -> None:
    """Count only regular digest records. Recover abandoned staging files after a grace period."""
    try:
        live: list[os.DirEntry[str]] = []
        with os.scandir(directory) as entries:
            for entry in entries:
                if entry.name.startswith("."):
                    if _STAGE.fullmatch(entry.name) and entry.is_file(follow_symlinks=False):
                        with contextlib.suppress(OSError):
                            if entry.stat(follow_symlinks=False).st_mtime < time.time() - _STAGE_GRACE:
                                os.unlink(entry.path)
                elif cap > 0 and _ENTRY.fullmatch(entry.name) and entry.is_file(follow_symlinks=False):
                    live.append(entry)
        excess = len(live) - cap
        if excess <= 0:
            return

        def stamp(entry: os.DirEntry[str]) -> tuple[int, str]:
            try:
                return entry.stat(follow_symlinks=False).st_mtime_ns, entry.name
            except FileNotFoundError:
                return -1, entry.name

        removed = 0
        for victim in heapq.nsmallest(excess, live, key=stamp):
            try:
                os.unlink(victim.path)
            except OSError:
                continue
            removed += 1
        logger.debug("cache sweep evicted=%d failed=%d", removed, excess - removed)
    except OSError:
        return


def lookup(
    settings: Settings,
    provider: ProviderName,
    scope: str,
    model: str,
    state: JsonValue,
    questions: Mapping[str, Question],
    *,
    digest: bytes | None = None,
) -> Evaluation | None:
    """The recorded evaluation for this exact request, or `None` (also when the cache is off)."""
    if not _enabled(settings):
        return None
    directory = cache_dir(settings)
    key = digest.hex() if digest is not None else request_key(provider, scope, model, state, questions)
    filename = f"{key}.json"
    try:
        fsutil.require_private_dir(directory)
        for parent in (directory / _RECORDS, directory):
            try:
                fsutil.require_private_dir(parent)
                handle = open(
                    parent / filename,
                    "rb",
                    opener=lambda name, flags: os.open(name, flags | os.O_NONBLOCK | os.O_NOFOLLOW),
                )
            except FileNotFoundError:
                continue
            with handle:
                info = os.fstat(handle.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_size > _RECORD_BYTES_MAX:
                    return None
                ttl = settings.cache_ttl_seconds
                if ttl > 0 and time.time() - info.st_mtime > ttl:
                    return None
                data = handle.read(info.st_size + 1)
                if len(data) != info.st_size:
                    return None
                parsed: object = decode_json(data.decode("utf-8"))
                break
        else:
            return None
    except (OSError, ValueError):
        return None
    if not isinstance(parsed, dict):
        return None
    record = cast(dict[str, object], parsed)
    answers = record.get("answers")
    usage = record.get("usage")
    name = record.get("provider")
    recorded_model = record.get("model")
    if not isinstance(answers, dict) or name != provider:
        return None
    # The envelope's own rule (finite, non-negative, bool is not a number): a record that the
    # provider path would have refused is a miss here, by the same test.
    counts = usage_counts(usage)
    if counts is None:
        return None
    # Old all-zero records cannot distinguish missing metering from an explicitly measured zero.
    reported = cast(dict[str, object], usage).get("reported", any(counts))
    if not isinstance(reported, bool):
        return None
    request_id = record.get("request_id")
    if request_id is not None and (not isinstance(request_id, str) or not 0 < len(request_id) <= _REQUEST_ID_MAX):
        return None
    logger.debug("cache hit")
    return Evaluation(
        answers=cast(dict[str, object], answers),
        usage=Usage(*counts, reported=reported),
        provider=provider,
        model=recorded_model if isinstance(recorded_model, str) else model,
        request_id=request_id,
    )


def store(
    settings: Settings,
    provider: ProviderName,
    scope: str,
    model: str,
    state: JsonValue,
    questions: Mapping[str, Question],
    evaluation: Evaluation,
    *,
    digest: bytes | None = None,
) -> None:
    """Record `evaluation` for this request. Off, or unwritable, means simply: not cached.

    Entries hold provider answers, usage, model and request provenance. Answers can contain sensitive text.
    """
    if not _enabled(settings) or (evaluation.request_id is not None and len(evaluation.request_id) > _REQUEST_ID_MAX):
        return
    # Keep the server's unsupported-platform diagnostic reachable before the first cache write.
    import fcntl

    directory = cache_dir(settings)
    key = digest.hex() if digest is not None else request_key(provider, scope, model, state, questions)
    record = {
        "answers": evaluation.answers,
        "usage": {
            "input_tokens": evaluation.usage.input_tokens,
            "output_tokens": evaluation.usage.output_tokens,
            "reported": evaluation.usage.reported,
        },
        "provider": evaluation.provider,
        "model": evaluation.model,
        "request_id": evaluation.request_id,
    }
    try:
        text = json.dumps(record, ensure_ascii=False)
        if len(text.encode("utf-8")) > _RECORD_BYTES_MAX:
            return
        fsutil.private_dir(directory)
        directory = fsutil.private_dir(directory / _RECORDS)
        fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            # Serialize publication and sweeping across cooperating writers, including active staging files.
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                logger.debug("cache publication busy; store skipped")
                return
            fsutil.write_private_atomic(directory / f"{key}.json", text)
            _evict(directory, settings.cache_max_entries)
        finally:
            os.close(fd)
    except (OSError, TypeError, ValueError):
        logger.debug("cache store skipped")


async def alookup(
    settings: Settings,
    provider: ProviderName,
    scope: str,
    model: str,
    state: JsonValue,
    questions: Mapping[str, Question],
    *,
    digest: bytes | None = None,
) -> Evaluation | None:
    """`lookup` off the event loop: the key serializes the whole state, so neither the hash nor
    the file read stalls concurrent calls while the cache is on (ADR-0047 amendment).

    With the cache off (the default) this returns before the thread hop: the off path schedules
    nothing, exactly as before the bounds.
    """
    if not _enabled(settings):
        return None
    return await anyio.to_thread.run_sync(
        lambda: lookup(settings, provider, scope, model, state, questions, digest=digest)
    )


async def astore(
    settings: Settings,
    provider: ProviderName,
    scope: str,
    model: str,
    state: JsonValue,
    questions: Mapping[str, Question],
    evaluation: Evaluation,
    *,
    digest: bytes | None = None,
) -> None:
    """`store` off the event loop, for the same reason as `alookup` (ADR-0047 amendment)."""
    if not _enabled(settings):
        return
    await anyio.to_thread.run_sync(
        lambda: store(settings, provider, scope, model, state, questions, evaluation, digest=digest)
    )
