"""State composition for `jev_ask`: own text, server-read files, and a gated command (ADR-0077).

Everything here is deterministic and precedes the ask's provider call, and most of it precedes any
provider call at all: questions are validated by the caller (`ask.py`) before this module runs, a
file read goes through `file_state.py` with all of its refusals, the command is first refused
deterministically when it matches the denylist — network clients, known secret stores, private
configuration directories, environment readers — and only then judged in-process with the command
hook's Bash questions (imported, never a subprocess of the CLI) before it runs, and an over-budget
composition refuses with a Split suggestion instead of truncating any part (`docs/CONTEXT.md`
"Split suggestion"). Every text that reaches the provider through a part is credential-redacted on
the way: the caller's own state with the ADR-0076 literal detector, command output with the hook's
shell-command redactor, the literal detector, and every configured secret value. The command
feature is off unless the operator enables it (`JEV_ASK_COMMANDS=1`); the child runs in an
environment scrubbed of every configured secret variable.
"""

import contextlib
import os
import re
import signal
import subprocess
import threading
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, NoReturn

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
from jev_judge_mcp.redact_action import REDACTED, redact_action
from jev_judge_mcp.settings import Settings
from jev_judge_mcp.text import length
from jev_judge_mcp.tools.base import ToolError
from jev_judge_mcp.tools.file_state import is_secret_store, read_state, resolve_scoped
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


def gate_state(command: str, redactions: Sequence[str] = ()) -> str:
    """The command, in the hook's own Bash state shape over a synthetic event.

    `redact_action` runs as in the hook, and every value in `redactions` (the server's configured
    secrets) is redacted too, so a command that embeds one never carries it to the judge.
    """

    def scrub(text: str) -> str:
        text = redact_action(text)
        for secret in redactions:
            if secret:
                text = text.replace(secret, REDACTED)
        return text

    event: dict[str, object] = {"cwd": str(Path.cwd()), "tool_name": "Bash", "tool_input": command}
    return event_state(event, None, "unknown", scrub)


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


_NETWORK_CLIENTS = frozenset(
    {"curl", "wget", "nc", "ncat", "netcat", "ssh", "scp", "sftp", "rsync", "ftp", "telnet", "socat"}
)
"""Network clients a refused command may not name: exfiltration does not need a destructive effect."""

_ENVIRONMENT_READERS = frozenset({"env", "printenv", "set", "export"})
"""Shell words that print or mutate the environment: the child env is scrubbed, and naming these
is refused outright so a scrubbed run is never probed for what survived."""

_PRIVATE_DIRECTORIES = ("~/.ssh", "~/.aws", "~/.config", "~/.pi", "~/.claude", "~/.codex")
"""Private configuration trees a refused command may not reference: the tilde form, the expanded
form, and any path that carries one of these names as a directory component (a `/Users/<other>/.aws`
spelling names the same tree)."""

_TOKEN_SPLIT = re.compile(r"[\s|;&()<>'\"]+")
"""Deterministic command tokenization: whitespace and the shell metacharacters that end a word.
Not a shell parser — a conservative splitter for a denylist, so a path inside quotes is still seen."""


def command_denylist_refusal(command: str) -> str | None:
    """The deterministic refusal for a command the denylist names, before any gate or run.

        A read-only effect is not a safe command: a network client can exfiltrate, a secret-store path
    can print a credential, and an environment reader can dump what the scrub missed. Every family
    refuses with the final-block notice and costs no provider call (ADR-0077 amendment, 2026-10-01).
    """
    tokens = [token for token in _TOKEN_SPLIT.split(command) if token]
    words = {os.path.basename(token).lower() for token in tokens}
    if words & _NETWORK_CLIENTS:
        return _refused("the command reaches the network")
    if words & _ENVIRONMENT_READERS:
        return _refused("the command reads or changes environment variables")
    lowered = command.lower()
    private_names = {name.lstrip("~/").lower() for name in _PRIVATE_DIRECTORIES}
    for directory in _PRIVATE_DIRECTORIES:
        if directory.lower() in lowered or os.path.expanduser(directory).lower() in lowered:
            return _refused("the command reaches a private configuration directory")
    if any(part.lower() in private_names for token in tokens for part in Path(token).parts):
        return _refused("the command reaches a private configuration directory")
    for token in tokens:
        if is_secret_store(Path(token)):
            return _refused("the command touches a known secret store")
    return None


def command_disabled_refusal() -> str:
    """The refusal every `command` argument gets while the operator has not enabled execution.

    Zero provider calls, zero execution — the refusal is the whole answer (ADR-0077 amendment,
    2026-10-01).
    """
    return (
        "command execution is disabled on this server; the operator can enable it with "
        f"JEV_ASK_COMMANDS=1 in the server environment. {FINAL_BLOCK_NOTICE}"
    )


def scrubbed_environment(settings: Settings) -> dict[str, str]:
    """The server's environment without every configured secret variable (ADR-0077 amendment).

    The child never inherits a credential by simply being spawned here; `named_secrets` pairs each
    value with its variable name, so only those variables are dropped.
    """
    secret_variables = {name for name, _ in settings.named_secrets()}
    return {name: value for name, value in os.environ.items() if name not in secret_variables}


def run_command(
    command: str,
    timeout_seconds: int,
    output_units_max: int,
    env: Mapping[str, str] | None = None,
    redactions: Sequence[str] = (),
) -> Part:
    """The judged command's redacted output as one part.

    Runs under the system shell with no stdin, in the working directory, in a scrubbed environment
    when the caller passes one, and its process group is killed at the timeout or as soon as the
    captured output passes the cap — the pipes are never allowed to buffer a flood (ADR-0077
    amendment). The output block carries the exit status and both streams, redacted with the
    hook's shell redactor, the ADR-0076 credential-literal detector, and every value in
    `redactions` (the server's own configured secrets) before it becomes state. The gate judged
    the command string; nothing here sandboxes the run.
    """
    # Worst-case UTF-16 encoding is three UTF-8 bytes per unit, so crossing this byte bound
    # proves the decoded text is over the cap: the flood kill is never a false kill.
    byte_cap = 3 * output_units_max + 3
    collected: dict[str, bytes] = {"stdout": b"", "stderr": b""}
    state = {"overflow": False}
    try:
        process = subprocess.Popen(  # noqa: S602 - the caller's shell command is the product (ADR-0077)
            command,
            shell=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(Path.cwd()),
            env=dict(env) if env is not None else None,
            start_new_session=True,
        )
    except (OSError, ValueError) as error:
        # ValueError: a command the OS refuses to spawn at all, such as an embedded NUL byte.
        raise ToolError(f"the command could not run ({error})", code="command_failed") from None

    if process.stdout is None or process.stderr is None:  # both pipes were requested
        raise AssertionError("unreachable: Popen(stdout=PIPE, stderr=PIPE)")

    def drain(name: str, stream: BinaryIO) -> None:
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = stream.read(65_536)
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > byte_cap:
                state["overflow"] = True
                break
        collected[name] = b"".join(chunks)
        stream.close()

    readers = [
        threading.Thread(target=drain, args=(name, stream), daemon=True)
        for name, stream in (("stdout", process.stdout), ("stderr", process.stderr))
    ]
    for reader in readers:
        reader.start()
    deadline = time.monotonic() + timeout_seconds
    timed_out = False
    while True:
        if state["overflow"]:
            _kill_group(process)
            break
        try:
            process.wait(timeout=0.05)
            break
        except subprocess.TimeoutExpired:
            pass
        if time.monotonic() > deadline:
            _kill_group(process)
            timed_out = True
            break
    for reader in readers:
        reader.join(timeout=5)
    if timed_out:
        raise ToolError(
            f"the command exceeded the {timeout_seconds}-second timeout and was killed; "
            "run a shorter or quieter command",
            code="command_timeout",
        )
    if state["overflow"]:
        raise ToolError(
            f"the command output passed the {output_units_max:,}-unit cap and the run was killed; "
            "run a command that prints less",
            code="output_too_large",
        )
    stdout = collected["stdout"].decode("utf-8", errors="replace")
    stderr = collected["stderr"].decode("utf-8", errors="replace")
    block = redact_credential_literals(
        redact_action(f"exit status: {process.returncode}\n--- stdout ---\n{stdout}\n--- stderr ---\n{stderr}")
    )
    for secret in redactions:
        if secret:
            block = block.replace(secret, REDACTED)
    units = length(block)
    if units > output_units_max:
        raise ToolError(
            f"the command output is {units:,} units, over the {output_units_max:,}-unit cap; "
            "re-run a command that prints less",
            code="output_too_large",
        )
    return Part(OUTPUT_PART, block, units)


def _kill_group(process: subprocess.Popen[bytes]) -> None:
    """Kill the whole process group: the shell dies with its children, so a piped grandchild
    cannot hold the output pipes open past the kill (the repository is POSIX-only, ADR-0032)."""
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(os.getpgid(process.pid), signal.SIGKILL)
    process.wait()


async def run_gated_command(command: str, settings: Settings) -> Part:
    """`run_command` with the ADR-owned budget, the scrubbed environment, and the server's own
    secrets added to the output redactions — off the event loop so a slow run blocks no other call."""
    return await anyio.to_thread.run_sync(
        run_command,
        command,
        ASK.command_timeout_seconds,
        ASK.command_output_units_max,
        scrubbed_environment(settings),
        tuple(settings.secret_values()),
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
