"""jev_verify: check claims against evidence (`index.ts:121-238`)."""

from typing import Any

from jev_judge_mcp.domain import ChoiceQuestion, Question
from jev_judge_mcp.ids import ensure_unique_ids
from jev_judge_mcp.limits import VERIFY
from jev_judge_mcp.policy import DEFAULT_AUTO_ACCEPT, fail_closed, verify_claim_action
from jev_judge_mcp.responses import claim_extras, summary_extras
from jev_judge_mcp.tools.base import JevTool, Runtime, ToolResult, caller_actions, define, frame, headline
from jev_judge_mcp.tools.common import EVIDENCE_SCHEMA, evidence_items, identify_evidence
from jev_judge_mcp.tools.observed import validate_choice
from jev_judge_mcp.tools.review_half import ANTI_INJECTION

RELATION_TO_VERDICT = {"supports": "verified", "contradicts": "contradicted", "says_nothing": "unsupported"}
"""`RELATION_TO_VERDICT` (`lib.ts:53-57`)."""

RELATION_CRITERIA = {
    "supports": "The evidence states the claim or directly implies that it is true",
    "contradicts": "The evidence states the opposite of the claim or implies that it is false",
    "says_nothing": "The evidence does not address what the claim asserts, either way",
}
NO_SOURCE_ID = "none"
NO_SOURCE = "No single evidence item contains the content the claim depends on"

ROLE_RULE = (
    " A claim is about the current state unless it says otherwise. Use after or current items for that claim,"
    " not before."
)
VERIFY_SUFFIX = ROLE_RULE + ANTI_INJECTION
"""Role rule plus the anti-injection sentence (ADR-0067, ADR-0068)."""

DEFINITION = define(
    "jev_verify",
    "Verify claims against evidence",
    "Check each claim against provided evidence text with TypeSafe Jev. Returns per claim: verdict (verified | "
    "contradicted | unsupported), full probability distribution, confidence, and whether the verdict stands on its "
    "own (auto) or needs human review. Pattern: docs.typesafe.ai/cookbooks/citation_check. Pass reports, PR "
    "descriptions, or agent briefs as claims and their cited sources, diffs, or documents as evidence.",
    {
        "type": "object",
        "properties": {
            "claims": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": VERIFY.claims_min,
                "description": "Claims to verify: each one a plain string, not an object, e.g. individual factual "
                "statements from a report.",
            },
            "evidence": EVIDENCE_SCHEMA,
            "auto_accept": {
                "type": "number",
                "minimum": 0,
                "maximum": 1,
                "description": "Verdicts at or above this confidence stand automatically; below it they are flagged "
                "'review'. Default 0.8.",
            },
        },
        "required": ["claims", "evidence"],
        "additionalProperties": False,
    },
)


async def handle(args: dict[str, Any], runtime: Runtime) -> ToolResult:
    auto_accept: float = args.get("auto_accept", DEFAULT_AUTO_ACCEPT)
    raw_evidence = evidence_items(args["evidence"])
    evidence = identify_evidence(raw_evidence, (NO_SOURCE_ID,) if len(raw_evidence) > 1 else ())
    claims = ensure_unique_ids([{"text": text} for text in args["claims"]], "claim").items

    questions: dict[str, Question] = {}
    for claim in claims:
        claim_id, text = claim["id"], claim["text"]
        questions[f"relation_{claim_id}"] = ChoiceQuestion(
            f"How does the evidence relate to claim `{claim_id}` ({text})?" + VERIFY_SUFFIX, RELATION_CRITERIA
        )
        if len(evidence) > 1:
            criteria: dict[str, str | None] = {str(item["id"]): None for item in evidence}
            criteria[NO_SOURCE_ID] = NO_SOURCE
            questions[f"source_{claim_id}"] = ChoiceQuestion(
                f"Which evidence item does claim `{claim_id}` ({text}) rest on?" + VERIFY_SUFFIX, criteria
            )

    state = {
        "purpose": "Verify each claim in claims against the evidence in evidence.",
        "claims": claims,
        "evidence": evidence,
    }
    evaluation = await runtime.ask(state, questions)
    answers = evaluation.answers
    source_keys = [*(str(item["id"]) for item in evidence), NO_SOURCE_ID]

    results: list[dict[str, object]] = []
    for claim in claims:
        relation = answers.get(f"relation_{claim['id']}")
        validated = validate_choice(relation, RELATION_TO_VERDICT)
        source_asked = len(evidence) > 1
        source = validate_choice(answers.get(f"source_{claim['id']}"), source_keys) if source_asked else None
        # Q4 (ADR-0012, ADR-0043): malformed confidence invalidates; absent confidence does not.
        if validated is None or validated.confidence_kind == "malformed":
            closed = fail_closed("verify")
            assert closed != "status"
            closed_row = {
                "id": claim["id"],
                "claim": claim["text"],
                "verdict": "unknown",
                "probabilities": None,
                "confidence": None,
                "status": "invalid_response",
                "action": closed,
                "supporting_evidence": None,
            }
            closed_row.update(claim_extras(closed_row, evidence))
            results.append(closed_row)
            continue
        confidence = validated.confidence
        support = source.choice if source is not None and source.choice != NO_SOURCE_ID else None
        policy_source = (None if source is None else source.choice) if source_asked else evidence[0]["id"]
        action = verify_claim_action(
            confidence,
            auto_accept,
            policy_source,
            evidence,
            source_invalid=source_asked and (source is None or source.confidence_kind == "malformed"),
        )
        row = {
            "id": claim["id"],
            "claim": claim["text"],
            "verdict": RELATION_TO_VERDICT[validated.choice],
            "probabilities": validated.probabilities,
            "confidence": confidence,
            "action": action,
            "supporting_evidence": support,
        }
        row.update(claim_extras(row, evidence, supporting=support))
        results.append(row)

    item_actions = caller_actions(r["action"] for r in results)
    return ToolResult(
        frame(
            "jev_verify",
            evaluation,
            {
                "auto_accept": auto_accept,
                "summary": {
                    "verified": sum(1 for r in results if r["verdict"] == "verified"),
                    "contradicted": sum(1 for r in results if r["verdict"] == "contradicted"),
                    "unsupported": sum(1 for r in results if r["verdict"] == "unsupported"),
                    "needs_review": sum(1 for r in results if r["action"] == "review"),
                    **summary_extras(results),
                },
                "results": results,
            },
        ),
        action=headline(item_actions),
        item_actions=item_actions,
    )


TOOL = JevTool(DEFINITION, handle)
