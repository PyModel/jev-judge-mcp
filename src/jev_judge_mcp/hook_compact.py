"""Opt-in compaction cut point: one Choice over clipped user turns after a compaction.

``jev-judge-mcp hook compact-cut`` is a short-lived process. It is not an MCP tool.
Claude Code fires SessionStart with source ``compact`` after a compaction; the hook asks
which user turn starts the live work and returns one line of additionalContext naming it,
so the summary keeps the live task. Stdout is empty when the hook abstains.
PreCompact cannot inject compaction instructions (ADR-0077); this is the documented path.
"""

import asyncio
import contextlib
from collections.abc import Mapping
from dataclasses import dataclass
from typing import cast

import anyio

from jev_judge_mcp.domain.json import decode_json, is_json_object
from jev_judge_mcp.domain.questions import ChoiceQuestion, Question
from jev_judge_mcp.hook import (
    ESTIMATED_CONFIDENCE_THRESHOLD,
    PROVIDER_TIMEOUT_SECONDS,
    REPORTED_CONFIDENCE_THRESHOLD,
)
from jev_judge_mcp.providers import JevProvider, ProviderError, ProviderTimeoutError
from jev_judge_mcp.serialize import stringify_compact
from jev_judge_mcp.text import head
from jev_judge_mcp.validation.choice import margin, validate_choice

TURNS_MAX = 20
"""User turns offered to the judgment: the newest ones only (ADR-0077)."""

TURN_UNITS_MAX = 1_000
"""UTF-16 units of a turn kept after clipping, so the whole state stays small (ADR-0077)."""

_QUESTION_ID = "cut"
_QUESTION = "Which user turn starts the live work this session is doing right now?"
_STATE_INTRO = (
    "A Claude Code session was just compacted: the conversation history was summarized, and the"
    " summary must carry the live work forward."
    " Below are the session's user turns, oldest first, each labeled by its turn id and clipped."
    " Judge only which turn is where the current live work begins."
)


@dataclass(frozen=True, slots=True)
class Turn:
    """One usable user turn: its real transcript id and its clipped text."""

    id: str
    text: str


def parse_turns(transcript: str) -> list[Turn]:
    """Usable user turns in transcript order. Broken lines and non-user lines are skipped.

    A turn needs a real transcript id (the Choice is keyed by it) and non-empty text.
    """
    turns: list[Turn] = []
    for line in transcript.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = decode_json(line)
        except ValueError:
            continue
        if not is_json_object(event):
            continue
        if event.get("type") != "user":
            continue
        identifier = event.get("uuid")
        text = _turn_text(event)
        if isinstance(identifier, str) and identifier and text:
            turns.append(Turn(id=identifier, text=text.strip()))
    return turns


def window(turns: list[Turn]) -> list[Turn]:
    """The newest ``TURNS_MAX`` turns, each clipped to ``TURN_UNITS_MAX`` UTF-16 units."""
    return [Turn(id=turn.id, text=head(turn.text, TURN_UNITS_MAX)) for turn in turns[-TURNS_MAX:]]


def build_question(turns: list[Turn]) -> dict[str, Question]:
    """One Choice keyed by the real turn ids; each option's description is the clipped turn."""
    return {_QUESTION_ID: ChoiceQuestion(instructions=_QUESTION, criteria={turn.id: turn.text for turn in turns})}


def build_state(turns: list[Turn]) -> str:
    lines = [_STATE_INTRO]
    lines.extend(f"[{turn.id}] {turn.text}" for turn in turns)
    return "\n".join(lines) + "\n"


def pick(answer: object, turns: list[Turn]) -> Turn | None:
    """The picked turn, or ``None`` when the answer does not earn a cut point.

    A missing or malformed answer is silence (fail-closed, no default turn), and so is a
    confident-too-low one: a wrong cut point must not ride into the summary.
    """
    identifiers = [turn.id for turn in turns]
    parsed = validate_choice(answer, identifiers)
    if parsed is None:
        return None
    confidence = parsed.confidence if parsed.confidence is not None else margin(parsed.probabilities)
    threshold = REPORTED_CONFIDENCE_THRESHOLD if parsed.confidence is not None else ESTIMATED_CONFIDENCE_THRESHOLD
    if confidence < threshold:
        return None
    for turn in turns:
        if turn.id == parsed.choice:
            return turn
    return None


def context_line(turn: Turn) -> str:
    """The one fold-in line: the picked turn's id and its text, flattened to a single line."""
    return f"Compaction cut point: live work starts at turn {turn.id}: {_flat(turn.text)}"


def render_context(line: str) -> str:
    """The SessionStart additionalContext envelope. These bytes are the hook contract."""
    payload = {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": line}}
    return stringify_compact(payload)


def judge(provider: JevProvider, turns: list[Turn], model: str) -> tuple[str | None, str]:
    """One provider call: ``(line, code)``. Any provider failure is ``(None, code)`` — silence."""
    try:
        return anyio.run(_ask, provider, turns, model)
    except BaseException as error:
        # The cancel type is only available inside the loop that just exited (hook.py's pattern).
        if isinstance(error, asyncio.CancelledError) or type(error).__name__ == "Cancelled":
            return None, "provider"
        raise


async def _ask(provider: JevProvider, turns: list[Turn], model: str) -> tuple[str | None, str]:
    try:
        try:
            evaluation = await provider.evaluate(
                build_state(turns), build_question(turns), model, PROVIDER_TIMEOUT_SECONDS
            )
        except ProviderTimeoutError:
            return None, "timeout"
        except ProviderError:
            return None, "provider"
        else:
            picked = pick(evaluation.answers.get(_QUESTION_ID), turns)
            return (None, "") if picked is None else (context_line(picked), "")
    finally:
        with anyio.CancelScope(shield=True):
            with contextlib.suppress(Exception):
                await provider.aclose()


def _turn_text(event: Mapping[str, object]) -> str:
    message = event.get("message")
    if not isinstance(message, dict):
        return ""
    content = cast(dict[str, object], message).get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in cast(list[object], content):
            if not isinstance(block, dict):
                continue
            record = cast(dict[str, object], block)
            text = record.get("text")
            if record.get("type") == "text" and isinstance(text, str):
                parts.append(text)
        return "\n".join(parts)
    return ""


def _flat(text: str) -> str:
    return " ".join(text.split())
