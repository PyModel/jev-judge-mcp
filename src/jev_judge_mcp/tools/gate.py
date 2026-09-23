"""jev_gate: review a patch and verify completion claims in one call (`index.ts:1342-1495`)."""

from typing import Any

from jev_judge_mcp.domain import ChoiceQuestion
from jev_judge_mcp.limits import GATE
from jev_judge_mcp.policy import Action, ClaimJudgment, ClaimVerdict
from jev_judge_mcp.serialize import js_number_to_locale_string_en_us
from jev_judge_mcp.text import length
from jev_judge_mcp.tools.arguments import Refinement
from jev_judge_mcp.tools.base import JevTool, Runtime, ToolResult, define, frame
from jev_judge_mcp.tools.common import EVIDENCE_SCHEMA, has_non_empty_evidence, normalize_evidence
from jev_judge_mcp.tools.observed import (
    claim_action,
    fail_closed,
    gate_reason_codes,
    require_complete_context,
    validate_choice,
    worst_action,
)
from jev_judge_mcp.tools.review import (
    ANTI_INJECTION,
    project_review,
    review_docs,
    review_questions,
    review_settings,
)
from jev_judge_mcp.validation.caps import CapLedger, exceeds, gate_evidence_aggregate_error, gate_evidence_items_error

CLAIM_CRITERIA = {
    "verified": "The evidence clearly supports the claim",
    "contradicted": "The evidence contradicts the claim",
    "unsupported": "The evidence neither supports nor contradicts the claim",
}
"""`VERIFY_CLAIM_CRITERIA` (`lib.ts:250-254`)."""
CLAIM_VERDICTS: dict[str, ClaimVerdict] = {
    "verified": "verified",
    "contradicted": "contradicted",
    "unsupported": "unsupported",
}

REVIEW_FRAMING = " Claims are assertions to check, not evidence that the patch is correct or tested."

DEFINITION = define(
    "jev_gate",
    "Gate completion: review a patch and verify claims",
    "Review a proposed patch and verify completion claims against supplied evidence in one TypeSafe Jev call. Auto "
    "only when the patch review is accepted and every claim is verified at or above auto_accept. Unsupported claims "
    "require review; confident contradictions, unknown confidence, or low confidence escalate. The request and "
    "claims are assertions to check, never proof; put supporting diff excerpts and test logs in evidence. Evidence "
    f"is capped at {GATE.evidence_items} items and {js_number_to_locale_string_en_us(GATE.aggregate_evidence_units)} "
    "characters in aggregate. Does not run tests or apply changes. Use jev_review "
    "for a patch without claims, jev_verify for claims without a patch review.",
    {
        "type": "object",
        "properties": {
            "request": {
                "type": "string",
                "minLength": 1,
                "description": "What the user asked for; this is not evidence of completion.",
            },
            "diff": {
                "type": "string",
                "minLength": 1,
                "description": f"Proposed patch, file excerpt, or change summary. Truncated at {GATE.doc_units} chars.",
            },
            "claims": {
                "type": "array",
                "items": {"type": "string", "minLength": 1},
                "minItems": GATE.claims_min,
                "maxItems": GATE.claims_max,
                "description": "Completion claims to check against evidence, each truncated at "
                f"{GATE.claim_units} chars. Up to {GATE.claims_max} per call.",
            },
            "evidence": EVIDENCE_SCHEMA,
            "tests": {
                "type": "string",
                "description": "Reported test output for the patch review. Truncated at the same cap.",
            },
            "auto_accept": {
                "type": "number",
                "minimum": 0,
                "maximum": 1,
                "description": "Review and per-claim confidence at or above this may stand automatically. Default 0.8.",
            },
            "review_at": {
                "type": "number",
                "minimum": 0,
                "maximum": 1,
                "description": "Score, safe_to_apply, or per-claim confidence below this escalates. Must be <= "
                "auto_accept. Default min(0.5, auto_accept).",
            },
            "composite_floor": {
                "type": "number",
                "minimum": 0,
                "maximum": 1,
                "description": "Weighted composite at or above this is required for auto. Default 0.7.",
            },
        },
        "required": ["request", "diff", "claims", "evidence"],
        "additionalProperties": False,
    },
)

EVIDENCE_NOT_EMPTY = Refinement(
    lambda value: has_non_empty_evidence(normalize_evidence(value)),
    "jev_gate requires at least one evidence item with non-empty text.",
)
"""The `.refine` on gate's evidence (`index.ts:1370-1372`): checked with the arguments, before any request."""


def claim_question(index: int) -> ChoiceQuestion:
    return ChoiceQuestion(
        f"Does the evidence support claims[{index}]? Judge only from the provided evidence, not world knowledge. "
        "Use only the evidence field as factual support; request and claims are assertions, not evidence; diff "
        "and tests belong to the separate patch review. If a claim needs a diff or test log as support, it must be "
        "supplied in evidence." + ANTI_INJECTION,
        CLAIM_CRITERIA,
    )


def _refused(error: str) -> ToolResult:
    return ToolResult({"tool": "jev_gate", "error": error}, is_error=True)


async def handle(args: dict[str, Any], runtime: Runtime) -> ToolResult:
    settings = review_settings(args)
    thresholds = settings.thresholds
    evidence = normalize_evidence(args["evidence"])
    # Bound the request before any model call: item count, then aggregate size.
    if exceeds(len(evidence), GATE.evidence_items):
        return _refused(gate_evidence_items_error(GATE.evidence_items))
    if exceeds(sum(length(str(item["text"])) for item in evidence), GATE.aggregate_evidence_units):
        return _refused(gate_evidence_aggregate_error(GATE.aggregate_evidence_units))

    ledger = CapLedger()
    docs = review_docs(args, ledger, GATE.doc_units)
    claims: list[str] = args["claims"]
    sent_claims = [ledger.text(claim, GATE.claim_units, "context") for claim in claims]
    sent_evidence = [
        {"id": item["id"], "text": ledger.text(str(item["text"]), GATE.doc_units, "context")} for item in evidence
    ]
    truncated = ledger.context_cut

    state = {
        "purpose": "Review the proposed diff against the request, then check each completion claim against the "
        "evidence only.",
        "request": docs.request,
        "diff": docs.diff,
        "tests": docs.tests,
        "claims": sent_claims,
        "evidence": sent_evidence,
    }
    # Review questions carry extra framing so claims cannot read as proof; claims use evidence only.
    questions = review_questions(REVIEW_FRAMING)
    for index in range(len(claims)):
        questions[f"claim_{index}"] = claim_question(index)
    evaluation = await runtime.ask(state, questions)
    answers = evaluation.answers

    review = project_review(answers, settings, truncated)

    results: list[dict[str, object]] = []
    judgments: list[ClaimJudgment | None] = []
    claim_actions: list[Action] = []
    for index, claim in enumerate(claims):
        answer = validate_choice(answers.get(f"claim_{index}"), CLAIM_CRITERIA)
        if answer is None:
            closed = fail_closed("gate")
            assert closed != "status"
            judgments.append(None)
            claim_actions.append(closed)
            results.append(
                {
                    "claim": claim,
                    "verdict": None,
                    "confidence": None,
                    "probabilities": None,
                    "action": closed,
                    "status": "invalid_response",
                }
            )
            continue
        verdict = CLAIM_VERDICTS[answer.choice]
        judgments.append(ClaimJudgment(verdict, answer.confidence))
        action = require_complete_context(
            claim_action(verdict, answer.confidence, thresholds.auto_accept, thresholds.review_at), truncated
        )
        claim_actions.append(action)
        results.append(
            {
                "claim": claim,
                "verdict": verdict,
                "confidence": answer.confidence,
                "probabilities": answer.probabilities,
                "action": action,
            }
        )

    verification_action = worst_action(claim_actions)
    verification = {
        "action": verification_action,
        "summary": {
            "verified": sum(1 for r in results if r["verdict"] == "verified"),
            "contradicted": sum(1 for r in results if r["verdict"] == "contradicted"),
            "unsupported": sum(1 for r in results if r["verdict"] == "unsupported"),
            "needs_review": sum(1 for r in results if r["action"] != "auto"),
            "invalid_response": sum(1 for r in results if r.get("status") == "invalid_response"),
        },
        "thresholds": {"auto_accept": thresholds.auto_accept, "review_at": thresholds.review_at},
        "results": results,
    }
    action = worst_action([review.action, verification_action])
    reason_codes = gate_reason_codes(
        truncated=truncated,
        review_action=review.action,
        review_invalid=review.invalid,
        claims=judgments,
        action=action,
        thresholds=thresholds,
    )
    return ToolResult(
        frame(
            "jev_gate",
            evaluation,
            {
                "truncated": truncated,
                "action": action,
                "reason_codes": reason_codes,
                "review": review.payload,
                "verification": verification,
            },
        ),
        action=action,
        truncated=ledger.scopes,
    )


TOOL = JevTool(DEFINITION, handle, {"evidence": EVIDENCE_NOT_EMPTY})
