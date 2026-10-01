"""State composition for `jev_ask`: own text, server-read files, and a gated command (ADR-0077).

Everything here is deterministic and precedes the ask's provider call, and most of it precedes any
provider call at all: questions are validated by the caller (`ask.py`) before this module runs, a
file read goes through `file_state.py` with all of its refusals, the command is judged in-process
with the command hook's Bash questions — imported, never a subprocess of the CLI — before it runs,
and an over-budget composition refuses with a Split suggestion instead of truncating any part
(`docs/CONTEXT.md` "Split suggestion"). Every text that reaches the provider through a part is
credential-redacted on the way: the caller's own state with the ADR-0076 literal detector, command
output with the hook's shell-command redactor plus the literal detector.
"""

import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import NoReturn

import anyio

from jev_judge_mcp.credential_literal import redact_credential_literals
from jev_judge_mcp.domain import ChoiceQuestion, NoulCriteria, NoulQuestion, Question
from jev_judge_mcp.hook import (
    DESTRUCTIVE_CRITERIA,
    DESTRUCTIVE_ID,
    DESTRUCTIVE_INTENT_THRESHOLD,
    DESTRUCTIVE_QUESTION,
    EFFECT_CRITERIA,
    EFFECT_ID,
    EFFECT_QUESTION,
    confidence_floor,
    event_state,
)
from jev_judge_mcp.hook_render import FINAL_BLOCK_NOTICE
from jev_judge_mcp.limits import ASK
from jev_judge_mcp.redact_action import redact_action
from jev_judge_mcp.text import length
from jev_judge_mcp.tools.base import ToolError
from jev_judge_mcp.tools.file_state import read_state, resolve_scoped
from jev_judge_mcp.tools.observed import validate_choice, validate_noul

OWN_PART = "state"
OUTPUT_PART = "command output"
"""The two fixed part names; file parts are named by the caller's path."""


@dataclass(frozen=True, slots=True)
class Part:
    """One composed state part: its display name, its redacted text, and its UTF-16 size."""

    name: str
    text: str
    units: int


@dataclass(frozen=True, slots=True)
class Skipped:
    """A path that never became state, with the file tool's typed refusal code."""

    path: str
    reason: str


def own_part(state_text: str) -> Part | None:
    """The caller's own framing text as one part, credential-redacted; `None` when it is empty.

    The text is sent verbatim but for redaction — whitespace included, so a judgment never sees
    text the caller did not write. The published schema rejects an over-cap `state` before the
    tool runs; this re-measures after redaction, which can grow a short secret into a
    `[redacted]` marker.
    """
    text = redact_credential_literals(state_text)
    if not text.strip():
        return None
    units = length(text)
    if units > ASK.state_units_max:
        raise ToolError(
            f"own state is {units:,} units, over the {ASK.state_units_max:,}-unit cap after redaction",
            code="input_too_large",
        )
    return Part(OWN_PART, text, units)


def file_parts(paths: Sequence[str]) -> tuple[list[Part], list[Skipped]]:
    """Each caller-named path as one part, or its typed skip; a read refusal never fails the call.

    Order is input order, exact duplicate paths are read once, and every refusal is
    `file_state.py`'s own typed code, reported per path in `skipped` — the batch's answer is the
    judgment over the parts that did read.
    """
    parts: list[Part] = []
    skipped: list[Skipped] = []
    seen: set[str] = set()
    for raw in paths:
        if raw in seen:
            continue
        seen.add(raw)
        try:
            content = read_state(resolve_scoped(raw))
        except ToolError as error:
            skipped.append(Skipped(raw, error.code))
            continue
        parts.append(Part(raw, content, length(content)))
    return parts, skipped


def gate_questions() -> dict[str, Question]:
    """The command hook's two Bash questions: what the command would do, and whether that is the intent."""
    return {
        EFFECT_ID: ChoiceQuestion(EFFECT_QUESTION, EFFECT_CRITERIA),
        DESTRUCTIVE_ID: NoulQuestion(DESTRUCTIVE_QUESTION, DESTRUCTIVE_CRITERIA),
    }


def gate_state(command: str) -> str:
    """The command, in the hook's own Bash state shape over a synthetic event, `redact_action` applied."""
    event: dict[str, object] = {"cwd": str(Path.cwd()), "tool_name": "Bash", "tool_input": command}
    return event_state(event, None, "unknown", redact_action)


def command_refusal(answers: Mapping[str, object]) -> str | None:
    """`None` when the gated command may run; the refusal reason otherwise.

    Strictly stricter than the hook, which also allows a reversible effect: an ask runs the command
    only over a read-only one, judged with the hook's confidence floors and its destructive-intent
    threshold. Every reason carries the final-block notice — the refusal is the answer (ADR-0077).
    """
    effect = validate_choice(answers.get(EFFECT_ID), tuple(EFFECT_CRITERIA))
    destructive = validate_noul(answers.get(DESTRUCTIVE_ID))
    if effect is None or destructive is None:
        return _refused("the gate's answers were malformed or missing")
    if destructive >= DESTRUCTIVE_INTENT_THRESHOLD:
        return _refused("the command looks aimed at destroying work", f"probability {destructive:.2f}")
    confidence, floor = confidence_floor(effect)
    if confidence < floor:
        return _refused("the gate could not tell what the command would do", f"confidence {confidence:.2f}")
    if effect.choice != "read_only":
        return _refused(f"the command looks {effect.choice}", f"confidence {confidence:.2f}")
    return None


def run_command(command: str, timeout_seconds: int, output_units_max: int) -> Part:
    """The judged command's redacted output as one part.

    Runs under the system shell with no stdin, in the working directory, killed at the timeout.
    The output block carries the exit status and both streams, redacted with the hook's shell
    redactor and the credential-literal detector before it becomes state; an over-cap output
    refuses after the run (nothing truncates). The gate judged the command string; nothing here
    sandboxes the run.
    """
    try:
        completed = subprocess.run(  # noqa: S602 - the caller's shell command is the product (ADR-0077)
            command,
            shell=True,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            cwd=str(Path.cwd()),
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise ToolError(
            f"the command exceeded the {timeout_seconds}-second timeout and was killed; "
            "run a shorter or quieter command",
            code="command_timeout",
        ) from None
    except (OSError, ValueError) as error:
        # ValueError: a command the OS refuses to spawn at all, such as an embedded NUL byte.
        raise ToolError(f"the command could not run ({error})", code="command_failed") from None
    stdout = completed.stdout.decode("utf-8", errors="replace")
    stderr = completed.stderr.decode("utf-8", errors="replace")
    block = redact_credential_literals(
        redact_action(f"exit status: {completed.returncode}\n--- stdout ---\n{stdout}\n--- stderr ---\n{stderr}")
    )
    units = length(block)
    if units > output_units_max:
        raise ToolError(
            f"the command output is {units:,} units, over the {output_units_max:,}-unit cap; "
            "re-run a command that prints less",
            code="output_too_large",
        )
    return Part(OUTPUT_PART, block, units)


async def run_gated_command(command: str) -> Part:
    """`run_command` with the ADR-owned budget, off the event loop so a slow run blocks no other call."""
    return await anyio.to_thread.run_sync(
        run_command, command, ASK.command_timeout_seconds, ASK.command_output_units_max
    )


def question_units(questions: Mapping[str, Question]) -> int:
    """UTF-16 units of every question on the wire: its id, its instructions, and its criteria text."""
    total = 0
    for identifier, question in questions.items():
        total += length(identifier) + length(str(question.instructions))
        criteria = question.criteria
        if isinstance(criteria, NoulCriteria):
            values = (criteria.true, criteria.false)
        elif isinstance(criteria, Mapping):
            values = tuple(criteria.values())
        else:
            values = tuple(criteria)
        total += sum(length(str(value)) for value in values)
    return total


def split_refusal(parts: Sequence[Part], questions_units: int) -> NoReturn:
    """The one over-budget refusal: every part's size, then a first-fit Split suggestion.

    The suggestion packs the parts, in composition order, into the fewest calls that each leave
    room for the questions; the same questions are re-asked on every call. When the questions
    alone are over the cap, or one part cannot fit beside them at all, the message says what to
    trim instead. A truncated judgment is never an alternative (ADR-0077).
    """
    state_units = sum(part.units for part in parts)
    lines = [
        f"jev_ask: the composed request is {state_units:,} units of state plus {questions_units:,} units "
        f"of questions, over the {ASK.request_units_max:,}-unit cap. Nothing is truncated.",
        "Per part: " + "; ".join(f"{part.name} {part.units:,}" for part in parts) + ".",
    ]
    if questions_units > ASK.request_units_max:
        lines.append("The questions alone exceed the cap; trim the question set (fewer or narrower questions).")
        raise ToolError(" ".join(lines), code="input_too_large")
    capacity = ASK.request_units_max - questions_units
    oversized = [part.name for part in parts if part.units > capacity]
    if oversized:
        lines.append(
            f"{' and '.join(oversized)} cannot fit beside the questions in one call "
            f"({capacity:,} units of room); shrink or drop that part."
        )
        raise ToolError(" ".join(lines), code="input_too_large")
    calls: list[list[str]] = []
    fill = 0
    for part in parts:
        if not calls or fill + part.units > capacity:
            calls.append([part.name])
            fill = part.units
        else:
            calls[-1].append(part.name)
            fill += part.units
    lines.append(
        "Split suggestion: "
        + "; ".join(f"call {index}: {', '.join(names)}" for index, names in enumerate(calls, start=1))
        + ". Re-ask the same questions on every call."
    )
    raise ToolError(" ".join(lines), code="input_too_large")


def _refused(failure: str, measure: str = "") -> str:
    tail = f" ({measure})" if measure else ""
    return f"command refused: {failure}{tail}. {FINAL_BLOCK_NOTICE}"
