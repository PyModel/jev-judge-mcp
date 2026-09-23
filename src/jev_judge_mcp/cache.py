"""The optional provider-response cache (ADR-0047): off unless `JEV_MCP_CACHE` is truthy.

An identical request — same provider, model, state, and questions — replays the recorded
`Evaluation` from a JSON file under the cache directory instead of calling the provider again, at
zero API cost. Replay is verbatim, so every tool payload built from it is byte-identical to the
first answer's; the cache never edits a response. Keep it off when decisions must stay fresh.

The key is the SHA-256 of the exact request body (`JSON.stringify` semantics, key order included),
so any difference — model slug, one character of state, a reordered question map — is a different
entry. Entries are mode 0600 inside a 0700 directory (`fsutil`): they hold the judged State. Writes
are atomic; a cache that cannot be read or written is a miss, never an error.
"""

import hashlib
import json
import logging
from collections.abc import Mapping
from pathlib import Path
from typing import cast

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


def lookup(
    settings: Settings, provider: ProviderName, model: str, state: JsonValue, questions: Mapping[str, Question]
) -> Evaluation | None:
    """The recorded evaluation for this exact request, or `None` (also when the cache is off)."""
    if not _enabled(settings):
        return None
    path = cache_dir(settings) / f"{_key(provider, model, state, questions)}.json"
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
