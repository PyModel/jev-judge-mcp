"""Build and redact `jev_ask` State from framing text, files and bounded read-only commands."""

import contextlib
import os
import re
import shlex
import shutil
import signal
import subprocess
import threading
import time
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum, auto
from pathlib import Path
from typing import BinaryIO, NoReturn

from jev_judge_mcp.credential_literal import redact_credential_literals
from jev_judge_mcp.domain import NoulCriteria, Question
from jev_judge_mcp.errors import Redactor
from jev_judge_mcp.hook_render import FINAL_BLOCK_NOTICE
from jev_judge_mcp.limits import ASK
from jev_judge_mcp.redact_action import redact_action
from jev_judge_mcp.settings import Settings
from jev_judge_mcp.text import length
from jev_judge_mcp.tools.base import ToolError
from jev_judge_mcp.tools.file_state import is_secret_store, read_state, resolve_scoped

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


def own_part(state_text: str, redact: Redactor | None = None) -> Part | None:
    """The caller's own framing text as one part, credential-redacted; `None` when it is empty.

    The text is sent verbatim but for redaction — whitespace included, so a judgment never sees
    text the caller did not write. The published schema rejects an over-cap `state` before the
    tool runs; this re-measures after redaction, which can grow a short secret into a
    `[redacted]` marker.
    """
    text = redact_credential_literals(state_text, redact)
    if not text.strip():
        return None
    units = length(text)
    if units > ASK.state_units_max:
        raise ToolError(
            f"own state is {units:,} units, over the {ASK.state_units_max:,}-unit cap after redaction",
            code="input_too_large",
        )
    return Part(OWN_PART, text, units)


def file_parts(
    paths: Sequence[str], key_path: Path | None = None, redact: Redactor | None = None
) -> tuple[list[Part], list[Skipped]]:
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
            content = read_state(resolve_scoped(raw), key_path=key_path, redact=redact)
        except ToolError as error:
            skipped.append(Skipped(raw, error.code))
            continue
        parts.append(Part(redact_credential_literals(raw, redact), content, length(content)))
    return parts, skipped


_PRIVATE_DIRECTORIES = frozenset({".ssh", ".aws", ".config", ".pi", ".claude", ".codex"})
_SHELL_OPERATORS = re.compile(r"""\\[\s\S]|'[^']*'|"(?:\\[\s\S]|[^"\\])*"|(?P<operator>[|;&<>()`\n])""")


class _Value(Enum):
    FLAG = auto()
    REQUIRED = auto()
    OPTIONAL_ATTACHED = auto()


_GIT_DIFF_OPTIONS = {
    **dict.fromkeys(("-p", "--name-only", "--name-status", "--no-color"), _Value.FLAG),
    **dict.fromkeys(("-U", "--unified"), _Value.REQUIRED),
    "--stat": _Value.OPTIONAL_ATTACHED,
}
_GIT_HISTORY_OPTIONS = {
    **_GIT_DIFF_OPTIONS,
    "--oneline": _Value.FLAG,
    **dict.fromkeys(("-n", "--max-count", "--since", "--until", "--author", "--format", "--pretty"), _Value.REQUIRED),
}
_GIT_OPTIONS = {
    "status": {
        **dict.fromkeys(("-s", "--short", "--branch"), _Value.FLAG),
        "--porcelain": _Value.OPTIONAL_ATTACHED,
    },
    "diff": {**_GIT_DIFF_OPTIONS, "--cached": _Value.FLAG, "--staged": _Value.FLAG},
    "log": _GIT_HISTORY_OPTIONS,
    "show": _GIT_HISTORY_OPTIONS,
}
_SEARCH_OPTIONS = {
    **dict.fromkeys(
        (
            "-i",
            "--ignore-case",
            "-w",
            "--word-regexp",
            "-F",
            "--fixed-strings",
            "-n",
            "--line-number",
            "-c",
            "--count",
            "-l",
            "--files-with-matches",
            "-v",
            "--invert-match",
            "-x",
            "--line-regexp",
        ),
        _Value.FLAG,
    ),
    **dict.fromkeys(
        (
            "-A",
            "--after-context",
            "-B",
            "--before-context",
            "-C",
            "--context",
            "-e",
            "--regexp",
            "-f",
            "--file",
            "-m",
            "--max-count",
        ),
        _Value.REQUIRED,
    ),
}
_COMMAND_OPTIONS: dict[str, dict[str, _Value]] = {
    "rg": {
        **_SEARCH_OPTIONS,
        **dict.fromkeys(("-s", "--case-sensitive", "-S", "--smart-case", "--hidden", "--no-ignore"), _Value.FLAG),
        **dict.fromkeys(("-g", "--glob", "--iglob", "-t", "--type", "-T", "--type-not"), _Value.REQUIRED),
    },
    "grep": {
        **_SEARCH_OPTIONS,
        **dict.fromkeys(("-r", "-R", "--recursive"), _Value.FLAG),
        **dict.fromkeys(("--include", "--exclude", "--exclude-dir"), _Value.REQUIRED),
    },
    "ls": dict.fromkeys(("-l", "-a", "-A", "-h", "-R", "-1", "-t", "-S"), _Value.FLAG),
    "wc": dict.fromkeys(("-l", "-w", "-c", "-m"), _Value.FLAG),
    "head": dict.fromkeys(("-n", "-c"), _Value.REQUIRED),
    "tail": dict.fromkeys(("-n", "-c"), _Value.REQUIRED),
}


def prepare_command(command: str, env: Mapping[str, str], key_path: Path | None) -> tuple[str, list[str]]:
    """Validate and pin the executable before any state read or judgment."""
    try:
        argv = shlex.split(command, comments=False, posix=True)
    except ValueError:
        _refuse("the command contains an incomplete word")
    if not argv or "\x00" in command:
        _refuse("the command needs a program name and cannot contain NUL")
    if any(match["operator"] for match in _SHELL_OPERATORS.finditer(command)):
        _refuse("unquoted shell operators are not allowed")
    program = argv[0]
    if program == "git":
        if len(argv) < 2 or argv[1] not in _GIT_OPTIONS:
            _refuse("git requires status, diff, log or show immediately after the program name")
        name = f"git {argv[1]}"
        options = _GIT_OPTIONS[argv[1]]
        prefix = argv[:2]
    elif program in _COMMAND_OPTIONS:
        name = program
        options = _COMMAND_OPTIONS[program]
        prefix = argv[:1]
    else:
        _refuse("the program is outside the read-only command allowlist")
    args, attached_values = _checked_arguments(name, argv[len(prefix) :], options)
    for word in (*argv, *attached_values):
        _check_path(word, key_path)

    executable = shutil.which(program, path=env.get("PATH", os.defpath))
    if executable is None:
        _refuse("the allowlisted executable was not found on PATH")
    try:
        found = Path(executable).absolute()
        resolved = found.resolve(strict=True)
        cwd = Path.cwd()
    except (OSError, RuntimeError):
        _refuse("the executable path could not be resolved")
    if found.is_relative_to(cwd) or resolved.is_relative_to(cwd):
        _refuse("the executable is inside the working directory")
    # Keep the selected program's name in argv[0] for multicall executables.
    return program, [str(resolved if resolved.name == program else found), *prefix[1:], *args]


def _checked_arguments(name: str, words: list[str], options: Mapping[str, _Value]) -> tuple[list[str], list[str]]:
    argv: list[str] = []
    attached_values: list[str] = []
    parse_options = True
    remaining = iter(words)
    for word in remaining:
        if parse_options and name == "tail" and word.startswith("+") and word[1:2].isdigit():
            _refuse_option(name, options)
        if not parse_options or word == "-" or not word.startswith("-"):
            argv.append(word)
        elif word == "--":
            parse_options = False
            argv.append(word)
        elif word.startswith("--"):
            flag, equals, value = word.partition("=")
            rule = options.get(flag)
            if rule is None or (equals and rule is _Value.FLAG):
                _refuse_option(name, options)
            if equals:
                attached_values.append(value)
            elif rule is _Value.REQUIRED:
                value = _take_option_value(remaining, name, options)
                equals = "="
            argv.append(f"{flag}{equals}{value}")
        else:
            for index, letter in enumerate(word[1:], start=2):
                flag = f"-{letter}"
                rule = options.get(flag)
                if rule is None:
                    _refuse_option(name, options)
                if rule is _Value.FLAG:
                    argv.append(flag)
                    continue
                attached = word[index:]
                if attached:
                    value = attached.removeprefix("=") if name == "rg" else attached
                    attached_values.append(value)
                else:
                    value = _take_option_value(remaining, name, options)
                # Bind values explicitly so an option-like value stays data in the child.
                if name == "rg":
                    argv.append(f"{flag}={value}")
                elif name.startswith("git ") or attached:
                    argv.append(f"{flag}{value}")
                else:
                    argv.extend((flag, value))
                break
    return argv, attached_values


def _take_option_value(remaining: Iterator[str], name: str, options: Mapping[str, _Value]) -> str:
    value = next(remaining, "--")
    if value == "--":
        _refuse_option(name, options)
    return value


def _refuse_option(name: str, options: Mapping[str, _Value]) -> NoReturn:
    _refuse(f"{name} requires a listed option with valid value syntax. Allowed options: {', '.join(options)}")


def _check_path(word: str, key_path: Path | None) -> None:
    path = Path(word).expanduser() if word.startswith("~/") else Path(word)
    # Git revisions can name files as REV:path.
    colon_path = word.rpartition(":")[2]
    paths = (path, Path(colon_path)) if colon_path and colon_path != word else (path,)
    for candidate in paths:
        try:
            resolved = candidate.resolve()
        except (OSError, RuntimeError, ValueError):
            _refuse("a command argument path could not be resolved")
        for spelling in (candidate, resolved):
            if any(part.lower() in _PRIVATE_DIRECTORIES for part in spelling.parts):
                _refuse("the command reaches a private directory")
            if is_secret_store(spelling, key_path):
                _refuse("the command touches a known secret store")


def git_command(argv: list[str], env: dict[str, str]) -> list[str]:
    """Build Git's argv under fixed policy and refuse configured worktree filters."""
    binary, subcommand, *args = argv
    env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull, GIT_NO_LAZY_FETCH="1", GIT_ALLOW_PROTOCOL="")
    git = [
        binary,
        "--no-pager",
        "--no-optional-locks",
        "-c",
        "core.fsmonitor=false",
        "-c",
        f"core.hooksPath={os.devnull}",
        "-c",
        "status.submoduleSummary=false",
        "-c",
        "submodule.recurse=false",
        "-c",
        "diff.submodule=short",
        "-c",
        "log.diffMerges=separate",
        "-c",
        "log.showSignature=false",
        "-c",
        f"gpg.openpgp.program={os.devnull}",
        "-c",
        f"gpg.x509.program={os.devnull}",
        "-c",
        f"gpg.ssh.program={os.devnull}",
    ]
    if subcommand in {"status", "diff"}:
        try:
            filters = subprocess.run(  # noqa: S603 - only the resolved Git and fixed options
                [*git, "config", "--includes", "--name-only", "--get-regexp", r"^filter\..*\.(clean|smudge|process)$"],
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=2,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            _refuse("Git's worktree filter configuration could not be checked")
        if filters.returncode != 1:
            _refuse("Git worktree filters are configured or could not be checked")
    flags = ["--ignore-submodules=all"]
    if subcommand != "status":
        flags += ["--no-ext-diff", "--no-textconv"]
    return [*git, subcommand, *flags, *args]


def command_disabled_refusal() -> str:
    """The refusal every `command` argument gets while the operator has not enabled execution.

    Zero provider calls, zero execution — the refusal is the whole answer (ADR-0077 amendment,
    2026-10-01).
    """
    return (
        "command execution is disabled on this server; the operator can enable it with "
        f"JEV_ASK_COMMANDS=1 in the server environment. {FINAL_BLOCK_NOTICE}"
    )


def scrubbed_environment(settings: Settings | None = None) -> dict[str, str]:
    """Only operating-system path, timezone, and locale settings reach the child."""
    allowed = {
        "PATH",
        "TZ",
        "LANG",
        "LANGUAGE",
        "LC_ALL",
        "LC_CTYPE",
        "LC_MESSAGES",
        "LC_NUMERIC",
        "LC_TIME",
        "LC_COLLATE",
        "LC_MONETARY",
    }
    if settings is not None:
        allowed.difference_update(name for name, _ in settings.named_secrets())
    return {name: os.environ[name] for name in allowed if name in os.environ}


def run_command(
    argv: Sequence[str],
    timeout_seconds: int,
    output_units_max: int,
    env: Mapping[str, str] | None = None,
    redactions: Sequence[str] = (),
) -> Part:
    """Capture the bounded, redacted output of a validated argv."""
    # Worst-case UTF-16 encoding is three UTF-8 bytes per unit, so crossing this byte bound
    # proves the decoded text is over the cap: the flood kill is never a false kill.
    byte_cap = 3 * output_units_max + 3
    collected: dict[str, bytes] = {"stdout": b"", "stderr": b""}
    state = {"overflow": False}
    try:
        process = subprocess.Popen(  # noqa: S603 - validated argv with an absolute executable
            argv,
            shell=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(Path.cwd()),
            env=dict(env) if env is not None else scrubbed_environment(),
            start_new_session=True,
        )
    except (OSError, ValueError) as error:
        # ValueError: a command the OS refuses to spawn at all, such as an embedded NUL byte.
        raise ToolError(f"the command could not run ({error})", code="command_failed") from None

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

    timed_out = False
    try:
        if process.stdout is None or process.stderr is None:  # both pipes were requested
            raise AssertionError("unreachable: Popen(stdout=PIPE, stderr=PIPE)")
        readers = [
            threading.Thread(target=drain, args=(name, stream), daemon=True)
            for name, stream in (("stdout", process.stdout), ("stderr", process.stderr))
        ]
        deadline = time.monotonic() + timeout_seconds
        for reader in readers:
            reader.start()
        while not state["overflow"]:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timed_out = True
                break
            try:
                process.wait(timeout=min(0.05, remaining))
                break
            except subprocess.TimeoutExpired:
                pass
    finally:
        _kill_group(process)
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
    if any(reader.is_alive() for reader in readers):
        # A child that left the group (its own session) still holds a pipe, so the output can never
        # be read to its end: refuse rather than return a part that is silently incomplete.
        raise ToolError(
            "the command left a process holding its output open after it exited; "
            "run a command whose children exit with it",
            code="command_failed",
        )
    stdout = collected["stdout"].decode("utf-8", errors="replace")
    stderr = collected["stderr"].decode("utf-8", errors="replace")
    block = redact_action(
        redact_credential_literals(
            f"exit status: {process.returncode}\n--- stdout ---\n{stdout}\n--- stderr ---\n{stderr}",
            Redactor(redactions),
        )
    )
    units = length(block)
    if units > output_units_max:
        raise ToolError(
            f"the command output is {units:,} units, over the {output_units_max:,}-unit cap; "
            "re-run a command that prints less",
            code="output_too_large",
        )
    return Part(OUTPUT_PART, block, units)


def _kill_group(process: subprocess.Popen[bytes]) -> None:
    """Kill the whole process group: the command dies with its children, so a piped grandchild
    cannot hold the output pipes open past the kill (the repository is POSIX-only, ADR-0032).

    `start_new_session=True` made the command's pid the group id, and a group id stays valid while
    any member lives — including after the command itself was reaped — so the pid is used directly;
    `os.getpgid` fails on a reaped leader and would turn the kill into a no-op.
    """
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(process.pid, signal.SIGKILL)
    process.wait()


def question_units(questions: Mapping[str, Question]) -> int:
    """UTF-16 units of caller ids, instructions, and criteria labels and descriptions."""
    total = 0
    for identifier, question in questions.items():
        total += length(identifier) + length(str(question.instructions))
        criteria = question.criteria
        if isinstance(criteria, NoulCriteria):
            values = (criteria.true, criteria.false)
        elif isinstance(criteria, Mapping):
            values = (*criteria, *criteria.values())
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


def _refuse(reason: str) -> NoReturn:
    raise ToolError(f"command refused: {reason}. {FINAL_BLOCK_NOTICE}", code="command_refused")
