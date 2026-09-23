"""jev_score: grade a subject on a caller-supplied ordered rubric (extension tool, ADR-0048).

The score question type was already first-class in the domain (`ScoreQuestion`, `validate_score`);
jev_review fixes its own four rubrics, and this tool hands the rubric to the caller: severity,
quality, risk — any ordered scale of 2-10 levels. One call, one rubric; the answer is a
probability-weighted position on the scale plus the full per-level distribution, never an
explanation. This tool is published beside the reference's ten (divergence `score-tool-extension`);
its caps live in `limits.SCORE` and are owned by ADR-0048, not by a parity-manifest block.
"""

from typing import Any

from jev_judge_mcp.domain import Question, ScoreQuestion
from jev_judge_mcp.limits import SCORE
from jev_judge_mcp.tools.base import JevTool, Runtime, ToolResult, define, frame
from jev_judge_mcp.tools.observed import validate_rubric_answer

INVALID = "invalid_response"

DEFINITION = define(
    "jev_score",
    "Grade on an ordered rubric",
    "Place a subject on a caller-supplied ordered scale of 2-10 levels with TypeSafe Jev: severity, risk, quality, "
    "confidence rubrics. Returns a fractional 0-based level index (1.5 sits between levels[1] and levels[2]), the "
    "nearest level index, the full per-level probability distribution, and confidence. Levels run low to high and "
    "are positions, so duplicate wording is allowed but confusing. Threshold the score in code; interpolation "
    "between levels is weakly calibrated. One call per unchanged subject and rubric; do not re-ask for a more "
    "pleasing number. Use jev_decide for bounded alternatives and jev_verify for claim-vs-evidence checks.",
    {
        "type": "object",
        "properties": {
            "subject": {
                "type": "string",
                "minLength": SCORE.subject_min,
                "maxLength": SCORE.subject_max,
                "description": f"What to grade. Rejected above {SCORE.subject_max:,} characters; send a bounded "
                "excerpt, not a whole document.",
            },
            "levels": {
                "type": "array",
                "items": {
                    "type": "string",
                    "minLength": SCORE.level_units_min,
                    "maxLength": SCORE.level_units_max,
                },
                "minItems": SCORE.levels_min,
                "maxItems": SCORE.levels_max,
                "description": f"The ordered rubric, low to high. {SCORE.levels_min}-{SCORE.levels_max} levels; each "
                "one line describing that position on the scale.",
            },
            "context": {
                "type": "string",
                "minLength": 1,
                "maxLength": SCORE.context_max,
                "description": "Optional background facts that inform the grading. Keep it short.",
            },
        },
        "required": ["subject", "levels"],
        "additionalProperties": False,
    },
)


async def handle(args: dict[str, Any], runtime: Runtime) -> ToolResult:
    levels: list[str] = args["levels"]
    questions: dict[str, Question] = {
        "grade": ScoreQuestion(
            "Where on the ordered rubric does the subject in the state fall, from the lowest to the highest level?",
            levels,
        )
    }
    state: dict[str, object] = {"subject": args["subject"], "levels": levels}
    if "context" in args:
        state["context"] = args["context"]
    evaluation = await runtime.ask(state, questions)

    answer = validate_rubric_answer(evaluation.answers.get("grade"), len(levels))
    if answer is None:
        body: dict[str, object] = {
            "levels": levels,
            "score": None,
            "nearest_level": None,
            "probabilities": None,
            "confidence": None,
            "status": INVALID,
        }
        return ToolResult(frame("jev_score", evaluation, body))
    # Ties go to the lower level: min() keeps the first minimum, and the keys are in level order.
    nearest = min(range(len(levels)), key=lambda index: abs(index - answer.score))
    body = {
        "levels": levels,
        "score": answer.score,
        "nearest_level": nearest,
        "probabilities": answer.probabilities,
        "confidence": answer.confidence,
        "status": "ok",
    }
    return ToolResult(frame("jev_score", evaluation, body))


TOOL = JevTool(definition=DEFINITION, handler=handle)
