"""jev_ask: the caller writes the questions over a composed state (extension tool, ADR-0077).

The coding-agent-helper centerpiece of the ask surface: one call carries every question the agent
wants about one state, and the state composes from three parts — the caller's own framing text,
files the server reads (the file tools' rules, `file_state.py`), and an optional command whose
output becomes state only after the command hook's gate judges it in-process. The questions are a
typed Noul/Choice/Score union keyed by caller ids, validated by the same builder the file tool
uses; the answers come back keyed by those ids, fail-closed per question. Overflow never
truncates: an over-budget composition refuses with each part's size and a Split suggestion.
Published after the frozen prefix (divergence `ask-tool-extension`); its caps live in
`limits.ASK` and are owned by ADR-0077.
"""

from typing import Any, cast

from jev_judge_mcp.domain import ChoiceQuestion, Question, ScoreQuestion
from jev_judge_mcp.limits import ASK, FILE_JUDGE, SANITIZE_ID_UNITS
from jev_judge_mcp.providers import Evaluation
from jev_judge_mcp.text import length
from jev_judge_mcp.tools.ask_state import (
    Part,
    command_denylist_refusal,
    command_disabled_refusal,
    command_refusal,
    file_parts,
    gate_questions,
    gate_state,
    own_part,
    question_units,
    run_gated_command,
    split_refusal,
)
from jev_judge_mcp.tools.base import JevTool, Runtime, ToolError, ToolResult, define, frame
from jev_judge_mcp.tools.file_judge import INVALID, build_question, project_answer
from jev_judge_mcp.tools.files import combined

KINDS = ("noul", "choice", "score")
"""The typed question union (ADR-0077 decision H2): typed objects, never a raw JSON string."""

_ON_DEMAND_RULE = (
    "Jev is invoked when an unresolved judgment earns a model decision. Deterministic evidence "
    "takes precedence; Jev is not a mandatory ceremony."
)

DEFINITION = define(
    "jev_ask",
    "Ask Jev questions you write over composed state",
    "Ask Jev questions you write about one composed state, and get typed answers without pasting bytes "
    "into your context. `questions` is an object keyed by your own ids; each value is one typed question: "
    "noul — the probability a yes/no condition holds — with optional `{true, false}` descriptions as "
    "`criteria`; choice — one option from `criteria`, an object of {option: description} entries; always "
    "give choice an `other` option; or score — a position on the ordered `criteria` levels, 2-10 strings "
    "low to high. Each question carries `type`, `instructions`, `criteria`. A typed answer beats open "
    "reasoning whenever the judgment can be enumerated: classification, gating, grading, yes/no. Put "
    "every question about one state into this one call; questions in one request cannot see each other's "
    "answers. The state composes from up to three parts: `state`, your own short framing text; `paths`, "
    "files the server reads as state — the file tools' rules apply (inside the working directory, known "
    "secret stores are never read, every read is credential-redacted); and `command`, a shell command the "
    "server runs only when the operator has enabled command execution with JEV_ASK_COMMANDS=1 in the "
    "server environment — without it, sending `command` is the typed `command_disabled` refusal, with no "
    "run and no call. When enabled, the command runs only after a deterministic denylist (network "
    "clients, secret stores, private config directories, environment readers) and the command hook's "
    "gate both pass: a read-only effect with confidence at the floor and no destructive intent. Anything "
    "else is the typed `command_refused` refusal carrying the reason and the final-block notice, with "
    "no execution and no ask call. The gate judges; this tool does not sandbox. An allowed command runs "
    "with no stdin, a 30-second timeout, a secret-scrubbed environment, and its redacted "
    "output becomes state. Nothing is ever truncated: an over-budget composition refuses with each "
    "part's size and a first-fit split suggestion naming which parts go to which call. Answers come "
    "back keyed by your ids, fail-closed per question. Not for exact lookups, counting, math, or "
    f"questions grep answers — run those and read the result; ask only when a judgment remains. "
    f"{_ON_DEMAND_RULE}",
    {
        "type": "object",
        "properties": {
            "questions": {
                "type": "object",
                "description": "Your questions, keyed by ids you choose (at most 64 characters each). Each "
                "value: {type: 'noul' | 'choice' | 'score', instructions: string, criteria: shaped by the "
                "type}. noul criteria: optional {true, false} descriptions. choice criteria: "
                f"{FILE_JUDGE.choice_options_min}-{FILE_JUDGE.choice_options_max} {{option: description}} "
                f"entries, descriptions at most {FILE_JUDGE.choice_option_units_max:,} units. score "
                f"criteria: the ordered level strings, {FILE_JUDGE.score_levels_min}-"
                f"{FILE_JUDGE.score_levels_max} of at most {FILE_JUDGE.score_level_units_max} units. "
                f"Instructions at most {FILE_JUDGE.instructions_units_max:,} units; every shape is "
                "validated before any read, run, or call.",
            },
            "state": {
                "type": "string",
                "minLength": 1,
                "maxLength": ASK.state_units_max,
                "description": f"Your own framing for the judgment, credential-redacted on the way to the "
                f"provider. Rejected above {ASK.state_units_max:,} characters; bulk material belongs in "
                "`paths`.",
            },
            "paths": {
                "type": "array",
                "items": {"type": "string", "minLength": 1},
                "maxItems": ASK.files_max,
                "description": f"Files to read as state, at most {ASK.files_max}. Same rules as "
                "jev_file_judge: resolved inside the working directory with symlinks followed, secret "
                "stores refused, binary and over-cap files skipped typed (in the payload's "
                "`state_summary.skipped`), reads credential-redacted.",
            },
            "command": {
                "type": "string",
                "minLength": 1,
                "description": "A shell command whose redacted output becomes one state part. Requires the "
                "operator's JEV_ASK_COMMANDS=1; without it the call refuses `command_disabled` with no run "
                "and no provider call. When enabled: a deterministic denylist (network clients, secret "
                "stores, private config directories, environment readers) and the command hook's gate both "
                "judge first — read-only effect, confidence at the floor, no destructive intent — or "
                "`command_refused`. The gate judges; this tool does not sandbox. 30-second timeout; "
                "over-cap output is killed and refuses `output_too_large`.",
            },
        },
        "required": ["questions"],
        "additionalProperties": False,
    },
)


def _build_questions(raw: object) -> dict[str, Question]:
    """Each caller-keyed question, validated before any read, run, or provider call.

    The published schema carries `questions` as an object the parser keeps whole (the argument
    parser takes no per-property constraints on a map, ADR-0022), so the typed union — kind
    membership, criteria shape, and every text bound, reusing the file tool's builder — is the
    same reject one layer down, typed `invalid_arguments`.
    """
    if not isinstance(raw, dict) or not raw:
        raise ToolError(
            f"questions is an object keyed by your ids, at least {ASK.questions_min} question.",
            code="invalid_arguments",
        )
    entries = cast("dict[object, object]", raw)
    if len(entries) > ASK.questions_max:
        raise ToolError(
            f"at most {ASK.questions_max} questions per call; ask the rest in a second call over the same state.",
            code="invalid_arguments",
        )
    questions: dict[str, Question] = {}
    for identifier_value, spec_value in entries.items():
        identifier = identifier_value if isinstance(identifier_value, str) else ""
        if not identifier or length(identifier) > SANITIZE_ID_UNITS:
            raise ToolError(
                f"question id must be a string of at most {SANITIZE_ID_UNITS} units.", code="invalid_arguments"
            )
        if not isinstance(spec_value, dict):
            raise ToolError(
                f"question `{identifier}` is an object with `type`, `instructions`, and `criteria`.",
                code="invalid_arguments",
            )
        spec = cast("dict[str, object]", spec_value)
        kind_value = spec.get("type")
        if not isinstance(kind_value, str) or kind_value not in KINDS:
            raise ToolError(
                f"question `{identifier}` type must be one of {', '.join(KINDS)}.", code="invalid_arguments"
            )
        kind = kind_value
        instructions = spec.get("instructions")
        if (
            not isinstance(instructions, str)
            or not instructions
            or length(instructions) > FILE_JUDGE.instructions_units_max
        ):
            raise ToolError(
                f"question `{identifier}` instructions must be a string of at most "
                f"{FILE_JUDGE.instructions_units_max:,} units.",
                code="invalid_arguments",
            )
        if "criteria" not in spec:
            raise ToolError(
                f"question `{identifier}` requires `criteria`, shaped by its type.", code="invalid_arguments"
            )
        questions[identifier] = build_question(kind, instructions, spec["criteria"])
    return questions


def _expected(question: Question) -> tuple[str, ...]:
    """The choice options, or the score levels; a noul expects nothing."""
    if isinstance(question, ChoiceQuestion | ScoreQuestion):
        return tuple(str(level) for level in question.criteria)
    return ()


async def handle(args: dict[str, Any], runtime: Runtime) -> ToolResult:
    questions = _build_questions(args["questions"])
    own = own_part(args.get("state", ""))
    parts: list[Part] = [own] if own is not None else []
    file_result, skipped = file_parts(args.get("paths", []))
    parts.extend(file_result)
    units = question_units(questions)
    command = args.get("command")
    if command is not None:
        # Two deterministic checks precede any provider call and any run: the operator's
        # execution flag (off by default, ADR-0077 amendment), then the denylist.
        if not runtime.settings.ask_commands:
            raise ToolError(command_disabled_refusal(), code="command_disabled")
        denylist = command_denylist_refusal(command)
        if denylist is not None:
            raise ToolError(denylist, code="command_refused")
    if command is not None and units + sum(part.units for part in parts) > ASK.request_units_max:
        # Hopeless before the gate: even an empty output cannot fit, so the command is not run
        # and no provider is built (deterministic evidence takes precedence).
        split_refusal(parts, units)
    if not parts and command is None:
        raise ToolError(
            "no state: give `state`, at least one readable `paths` entry, or a `command`.",
            code="invalid_arguments",
        )
    evaluations: list[Evaluation] = []
    output: Part | None = None
    if command is not None:
        gate = await runtime.ask(
            {"proposed_action": gate_state(command, runtime.settings.secret_values())}, gate_questions()
        )
        evaluations.append(gate)
        reason = command_refusal(gate.answers)
        if reason is not None:
            raise ToolError(reason, code="command_refused")
        output = await run_gated_command(command, runtime.settings)
        parts.append(output)
    if units + sum(part.units for part in parts) > ASK.request_units_max:
        split_refusal(parts, units)
    state = _named_state(parts)
    evaluation = await runtime.ask(state, questions)
    evaluations.append(evaluation)
    answers: dict[str, Any] = {}
    statuses: list[str] = []
    for identifier, question in questions.items():
        answer, status = project_answer(question.type, evaluation.answers.get(identifier), _expected(question))
        answers[identifier] = {**answer, "status": status}
        statuses.append(status)
    body: dict[str, object] = {
        "answers": answers,
        "status": "ok" if all(status == "ok" for status in statuses) else INVALID,
        "state_summary": {
            "own": own.units if own is not None else 0,
            "files": len(file_result),
            "output": output.units if output is not None else 0,
            "skipped": [{"path": skip.path, "reason": skip.reason} for skip in skipped],
            "units": units + sum(part.units for part in parts),
        },
    }
    return ToolResult(frame("jev_ask", evaluations[0] if len(evaluations) == 1 else combined(evaluations), body))


def _named_state(parts: list[Part]) -> dict[str, str]:
    """The parts as named state fields; a name already taken gets a `_2`-style suffix.

    File parts are named by the caller's path, so a file literally called `state` must not
    silently replace the caller's own framing part, and the suffix itself is never trusted
    free: the loop re-checks until the name is fresh.
    """
    state: dict[str, str] = {}
    for part in parts:
        name = part.name
        counter = 1
        while name in state:
            counter += 1
            name = f"{part.name}_{counter}"
        state[name] = part.text
    return state


TOOL = JevTool(DEFINITION, handle)
