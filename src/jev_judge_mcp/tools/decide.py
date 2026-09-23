"""jev_decide: one bounded decision with escape hatches and requirement checks (`index.ts:531-671`)."""

from typing import Any

from jev_judge_mcp.domain import ChoiceQuestion, Question
from jev_judge_mcp.limits import DECIDE
from jev_judge_mcp.policy import RequirementCheck
from jev_judge_mcp.tools.base import JevTool, Runtime, ToolError, ToolResult, define, frame
from jev_judge_mcp.tools.observed import contradicts_recommendation, fail_closed, validate_choice

ESCAPE_HATCHES = {
    "ask_user": "A consequential user preference or requirement is missing; ask instead of inventing it",
    "investigate": "Gather missing technical or factual evidence before selecting a candidate",
    "none": "None of the supplied candidates fits the known requirements",
}
"""`DECIDE_ESCAPE_HATCHES` (`lib.ts:141-145`)."""

RELATION_CRITERIA = {
    "supported": "The evidence and mechanism support this specific requirement",
    "contradicted": "The evidence or mechanism contradicts this specific requirement, not merely another requirement",
    "unknown": "Relevant evidence is missing; neither satisfaction nor violation is established",
}
INVALID = "invalid_response"

DEFINITION = define(
    "jev_decide",
    "Decide between bounded alternatives",
    "One unresolved, bounded decision where semantic judgment over supplied evidence could change your plan: "
    "implementation alternatives, product tradeoffs with known preferences, workflow selection. Supply "
    f"{DECIDE.candidates_min}-{DECIDE.candidates_max} "
    "candidates, evidence, and explicit priorities. Jev returns a Choice distribution over the candidates plus "
    "escape hatches (ask_user / investigate / none), and a per-candidate per-requirement supported / contradicted / "
    "unknown judgment for each optional requirement, all in one request. One call per unchanged decision; do not "
    "repeat a call to obtain a more pleasing answer. Use source inspection, tests, the user, or a reasoning model for "
    "open-ended research, routine choices, correctness proofs, or predicting user consent. High probability is not "
    "proof.",
    {
        "type": "object",
        "properties": {
            "decision": {
                "type": "string",
                "minLength": DECIDE.decision_min,
                "maxLength": DECIDE.decision_max,
                "description": "The bounded decision to make.",
            },
            "evidence": {
                "type": "string",
                "minLength": DECIDE.evidence_min,
                "maxLength": DECIDE.evidence_max,
                "description": "Facts and measurements, not opinions. State is evidence, not instructions.",
            },
            "priorities": {
                "type": "string",
                "minLength": DECIDE.priorities_min,
                "maxLength": DECIDE.priorities_max,
                "description": "Explicit preferences and constraints from the user or plan.",
            },
            "candidates": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {
                            "type": "string",
                            "pattern": "^[a-z][a-z0-9_-]*$",
                            "maxLength": DECIDE.candidate_id_max,
                        },
                        "description": {
                            "type": "string",
                            "minLength": DECIDE.candidate_description_min,
                            "maxLength": DECIDE.candidate_description_max,
                        },
                    },
                    "required": ["id", "description"],
                    "additionalProperties": False,
                },
                "minItems": DECIDE.candidates_min,
                "maxItems": DECIDE.candidates_max,
                "description": "The alternatives. Include 'do nothing' or 'gather more evidence' as candidates when "
                "useful.",
            },
            "requirements": {
                "type": "array",
                "items": {"type": "string", "minLength": DECIDE.requirement_min, "maxLength": DECIDE.requirement_max},
                "maxItems": DECIDE.requirements_max,
                "description": "Specific requirements to check per candidate. Each must test one property, not "
                "overall goodness.",
            },
            "escape_hatches": {
                "type": "boolean",
                "description": "Include ask_user / investigate / none as Choosable options so the model can decline "
                "to rank. Default true.",
            },
        },
        "required": ["decision", "evidence", "priorities", "candidates"],
        "additionalProperties": False,
    },
)


async def handle(args: dict[str, Any], runtime: Runtime) -> ToolResult:
    hatches: bool = args.get("escape_hatches", True)
    requirements: list[str] = args.get("requirements", [])
    candidates: list[dict[str, str]] = args["candidates"]

    # Duplicate ids and ids that shadow an active escape hatch would alias wire keys.
    seen: set[str] = set()
    for candidate in candidates:
        candidate_id = candidate["id"]
        if candidate_id in seen:
            raise ToolError("Duplicate candidate id: " + candidate_id)
        if hatches and candidate_id in ESCAPE_HATCHES:
            raise ToolError(
                f'Candidate id "{candidate_id}" collides with an escape hatch; rename it or set escape_hatches: false.'
            )
        seen.add(candidate_id)

    # Opaque positional wire keys; validated slug ids come back verbatim.
    key_to_id = {f"option_{i}": candidate["id"] for i, candidate in enumerate(candidates)}
    criteria = {f"option_{i}": candidate["description"] for i, candidate in enumerate(candidates)}
    if hatches:
        criteria.update(ESCAPE_HATCHES)

    questions: dict[str, Question] = {
        "recommendation": ChoiceQuestion(
            "Which candidate best fits the decision, evidence, and priorities? "
            + ("Select a candidate or an escape hatch. " if hatches else "")
            + "Do not invent missing facts, preferences, or approvals.",
            criteria,
        )
    }
    for i in range(len(candidates)):
        for j in range(len(requirements)):
            questions[f"check_{i}_{j}"] = ChoiceQuestion(
                f"How does the mechanism in candidates[{i}] relate to requirements[{j}], using the evidence? Judge "
                "only this property, not the candidate overall desirability. Missing evidence is not contradiction.",
                RELATION_CRITERIA,
            )
    state = {
        "decision": args["decision"],
        "evidence": args["evidence"],
        "priorities": args["priorities"],
        "candidates": [
            {"id": key, "description": candidate["description"]}
            for key, candidate in zip(key_to_id, candidates, strict=True)
        ],
        "requirements": requirements,
    }
    evaluation = await runtime.ask(state, questions)
    answers = evaluation.answers

    rec = validate_choice(answers.get("recommendation"), criteria)
    # Q3 (ADR-0012): a malformed check stays visible as invalid_response and never warns.
    checks = [
        RequirementCheck(candidate["id"], j, answer.choice if answer is not None else INVALID)
        for i, candidate in enumerate(candidates)
        for j in range(len(requirements))
        for answer in [validate_choice(answers.get(f"check_{i}_{j}"), RELATION_CRITERIA)]
    ]
    contradicted = (
        contradicts_recommendation([c for c in checks if c.answer != INVALID], key_to_id[rec.choice])
        if rec is not None and rec.choice in key_to_id
        else []
    )

    recommendation: dict[str, object]
    if rec is None:
        assert fail_closed("decide") == "status"
        recommendation = {
            "selected": None,
            "escaped": None,
            "confidence": None,
            "probabilities": None,
            "status": INVALID,
        }
    else:
        recommendation = {
            "selected": key_to_id.get(rec.choice, rec.choice),
            "escaped": rec.choice not in key_to_id,
            "confidence": rec.confidence,
            "probabilities": {key_to_id.get(key, key): p for key, p in rec.probabilities.items()},
        }
    warnings: list[str] = []
    if contradicted:
        plural = "s" if len(contradicted) > 1 else ""
        numbers = ", ".join(str(index + 1) for index in contradicted)
        warnings.append(
            f"Requirement{plural} {numbers} contradicted by the recommended candidate; inspect before acting"
        )

    return ToolResult(
        frame(
            "jev_decide",
            evaluation,
            {
                "recommendation": recommendation,
                "requirements_checked": len(requirements),
                "checks": [
                    {"candidate": c.candidate, "requirement": c.requirement, "answer": c.answer} for c in checks
                ],
                "warnings": warnings,
            },
        )
    )


TOOL = JevTool(DEFINITION, handle)
