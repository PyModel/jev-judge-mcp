"""jev_file_judge: one kind-discriminated judgment about a file the server reads (ADR-0077).

The agent names a path and writes one question; the server reads the file as state and returns the
typed answer, so the file's bytes never enter the agent's context. This is the extension-tool path
ADR-0048 opened: it is published after the frozen prefix (divergence `file-judge-tools`), its caps
live in `limits.FILE_JUDGE` and are owned by ADR-0077, and its refusals are typed verdicts about
the input — never judgments, and never a provider call. The reading and refusal rules live in
`file_state.py`, which `jev_files_judge` reuses.
"""

from collections.abc import Sequence
from typing import Any, cast

from jev_judge_mcp.domain import ChoiceQuestion, NoulCriteria, NoulQuestion, Question, ScoreQuestion
from jev_judge_mcp.domain.json import JsonValue
from jev_judge_mcp.limits import FILE_JUDGE
from jev_judge_mcp.providers import Evaluation
from jev_judge_mcp.text import length
from jev_judge_mcp.tools.arguments import Refinement
from jev_judge_mcp.tools.base import JevTool, Runtime, ToolError, ToolResult, define, frame
from jev_judge_mcp.tools.file_state import read_state, resolve_scoped
from jev_judge_mcp.tools.observed import validate_choice, validate_noul, validate_rubric_answer

INVALID = "invalid_response"

KINDS = ("noul", "choice", "score")
QUESTION_ID = "file"

KIND_REFINEMENT = Refinement(
    lambda value: value in KINDS,
    f"kind must be one of {', '.join(KINDS)}.",
)
"""The `.refine` on `kind`: the argument parser takes no enum keyword (ADR-0022), so membership is
enforced with the arguments, before any handler code."""

_ON_DEMAND_RULE = (
    "Jev is invoked when an unresolved judgment earns a model decision. Deterministic evidence "
    "takes precedence; Jev is not a mandatory ceremony."
)

DEFINITION = define(
    "jev_file_judge",
    "Judge a file you have not read",
    "Answer one question about a file without reading it: name a path, pick the question kind — noul (the "
    "probability a yes/no condition holds), choice (one option from `criteria`, an object of {option: "
    "description} entries), or score (a position on the ordered `criteria` levels, low to high) — and write "
    "`instructions` against the state's `content` field. The server reads the file as state and returns only "
    "the typed answer; the file's bytes never enter your context. The path must resolve inside the server's "
    "working directory, symlinks followed, with no override. Refusals are typed — not_found, not_a_file, "
    "binary_file, secret_file, file_too_large, path_outside_scope — and make no provider call: known secret "
    "stores (*.env and .env files, *.pem, *.key, id_rsa, and the like) are never read, and every read is "
    "credential-literal "
    "redacted before it is judged. Use jev_files_judge for many files at once. Not for exact lookups, "
    "counting, math, or questions grep answers — run the command instead. "
    f"{_ON_DEMAND_RULE}",
    {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "minLength": 1,
                "description": "The file to judge, relative to the server's working directory. Resolved with "
                "symlinks followed; a path that escapes the directory is refused, and there is no override.",
            },
            "kind": {
                "type": "string",
                "description": f"The question type: one of {', '.join(KINDS)}.",
            },
            "instructions": {
                "type": "string",
                "minLength": 1,
                "maxLength": FILE_JUDGE.instructions_units_max,
                "description": f"The question, asked against the state's `content` field. Rejected above "
                f"{FILE_JUDGE.instructions_units_max:,} characters.",
            },
            "criteria": {
                "anyOf": [
                    {"type": "object"},
                    {
                        "type": "array",
                        "items": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": FILE_JUDGE.score_level_units_max,
                        },
                        "minItems": FILE_JUDGE.score_levels_min,
                        "maxItems": FILE_JUDGE.score_levels_max,
                    },
                ],
                "description": "Shaped by `kind`. noul: an object with optional `true` and `false` keys, each a "
                f"description of that outcome, at most {FILE_JUDGE.instructions_units_max:,} units. choice: an "
                f"object of {FILE_JUDGE.choice_options_min}-{FILE_JUDGE.choice_options_max} {{option: description}} "
                f"entries, each description at most {FILE_JUDGE.choice_option_units_max:,} units. score: the "
                f"ordered levels, {FILE_JUDGE.score_levels_min}-{FILE_JUDGE.score_levels_max} strings of "
                f"{FILE_JUDGE.score_level_units_max} units each, low to high.",
            },
        },
        "required": ["path", "kind", "instructions", "criteria"],
        "additionalProperties": False,
    },
)


def build_question(kind: str, instructions: str, criteria: object) -> Question:
    """The one question, with `criteria` validated against its kind — before any I/O or provider call.

    The published schema carries the score array's bounds, but a Record's per-entry bounds cannot be
    expressed in it (the argument parser takes no per-property constraints on a keep-whole object,
    ADR-0022), so the choice and noul shapes are refused here, typed `invalid_arguments`, before any
    provider call exists.
    """
    if kind == "score":
        levels = cast("list[object]", criteria) if isinstance(criteria, list) else None
        if levels is None or not all(isinstance(level, str) for level in levels):
            raise ToolError(
                f"criteria for kind score is the ordered array of {FILE_JUDGE.score_levels_min}-"
                f"{FILE_JUDGE.score_levels_max} level strings; send an array, not an object.",
                code="invalid_arguments",
            )
        return ScoreQuestion(instructions, cast("list[str]", levels))
    if not isinstance(criteria, dict):
        shape = (
            "an object with optional `true` and `false` keys"
            if kind == "noul"
            else "an object of {option: description} entries"
        )
        raise ToolError(f"criteria for kind {kind} is {shape}; send an object, not an array.", code="invalid_arguments")
    entries = cast("dict[str, JsonValue]", criteria)
    if kind == "noul":
        unknown = sorted(set(entries) - {"true", "false"})
        if unknown:
            raise ToolError(
                f"noul criteria knows only the keys `true` and `false`, got: {', '.join(unknown)}",
                code="invalid_arguments",
            )
        descriptions: dict[str, str | None] = {}
        for key in ("true", "false"):
            value = entries.get(key)
            if value is not None and (not isinstance(value, str) or length(value) > FILE_JUDGE.instructions_units_max):
                raise ToolError(
                    f"noul criteria `{key}` must be a description of at most "
                    f"{FILE_JUDGE.instructions_units_max:,} units.",
                    code="invalid_arguments",
                )
            descriptions[key] = value
        return NoulQuestion(instructions, NoulCriteria(true=descriptions["true"], false=descriptions["false"]))
    if kind == "choice":
        options = list(entries)
        if not FILE_JUDGE.choice_options_min <= len(options) <= FILE_JUDGE.choice_options_max:
            raise ToolError(
                f"choice criteria needs {FILE_JUDGE.choice_options_min}-{FILE_JUDGE.choice_options_max} options, "
                f"got {len(options)}.",
                code="invalid_arguments",
            )
        for option, description in entries.items():
            if not isinstance(description, str) or length(description) > FILE_JUDGE.choice_option_units_max:
                raise ToolError(
                    f"choice criteria `{option}` must be a description of at most "
                    f"{FILE_JUDGE.choice_option_units_max:,} units.",
                    code="invalid_arguments",
                )
        return ChoiceQuestion(instructions, dict(entries))
    raise ToolError(f"kind must be one of {', '.join(KINDS)}, got {kind!r}.", code="invalid_arguments")


def _project(kind: str, evaluation: Evaluation, expected: Sequence[str]) -> tuple[dict[str, Any], str]:
    """The typed answer payload and its status: fail-closed, with no default verdict (ADR-0077).

    `expected` is the choice option list; the score projection reads its length as the rubric.
    """
    raw = evaluation.answers.get(QUESTION_ID)
    if kind == "noul":
        value = validate_noul(raw)
        return ({"noul": value}, "ok" if value is not None else INVALID)
    if kind == "choice":
        answer = validate_choice(raw, expected)
        if answer is None:
            return ({"choice": None, "probabilities": None, "confidence": None}, INVALID)
        return ({"choice": answer.choice, "probabilities": answer.probabilities, "confidence": answer.confidence}, "ok")
    answer = validate_rubric_answer(raw, len(expected))
    if answer is None:
        return ({"score": None, "nearest_level": None, "probabilities": None, "confidence": None}, INVALID)
    # Ties go to the lower level: min() keeps the first minimum, and the keys are in level order.
    # The levels themselves are the caller's own input and are not echoed: the payload carries the
    # typed answer only.
    nearest = min(range(len(expected)), key=lambda index: abs(index - answer.score))
    body = {
        "score": answer.score,
        "nearest_level": nearest,
        "probabilities": answer.probabilities,
        "confidence": answer.confidence,
    }
    return (body, "ok")


async def handle(args: dict[str, Any], runtime: Runtime) -> ToolResult:
    kind: str = args["kind"]
    question = build_question(kind, args["instructions"], args["criteria"])
    path = resolve_scoped(args["path"])
    content = read_state(path)
    evaluation = await runtime.ask({"path": args["path"], "content": content}, {QUESTION_ID: question})
    expected: Sequence[str] = ()
    if isinstance(question, ChoiceQuestion):
        expected = list(question.criteria)
    elif isinstance(question, ScoreQuestion):
        expected = cast("Sequence[str]", question.criteria)
    answer, status = _project(kind, evaluation, expected)
    body = {"path": args["path"], "kind": kind, "answer": answer, "status": status}
    return ToolResult(frame("jev_file_judge", evaluation, body))


TOOL = JevTool(DEFINITION, handle, {"kind": KIND_REFINEMENT})
