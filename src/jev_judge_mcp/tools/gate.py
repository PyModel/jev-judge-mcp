"""jev_gate: review a patch and verify completion claims in one call (`index.ts:1342-1495`)."""

from typing import Any, cast

from jev_judge_mcp.domain import ChoiceQuestion
from jev_judge_mcp.ids import ensure_unique_ids
from jev_judge_mcp.limits import GATE
from jev_judge_mcp.policy import Action, ClaimJudgment, ClaimVerdict
from jev_judge_mcp.policy.claims import note_blocks_auto
from jev_judge_mcp.responses import (
    caller_renames,
    claim_extras,
    next_checks_for,
    renamed_ids_field,
    summary_extras,
)
from jev_judge_mcp.serialize import js_number_to_locale_string_en_us
from jev_judge_mcp.text import length
from jev_judge_mcp.tools.arguments import Refinement
from jev_judge_mcp.tools.base import JevTool, Runtime, ToolError, ToolResult, define, frame
from jev_judge_mcp.tools.common import EVIDENCE_SCHEMA, evidence_items, has_non_empty_evidence, normalize_evidence
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
from jev_judge_mcp.tools.verify import NO_SOURCE, ROLE_RULE, VERIFY_SUFFIX
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
                "description": (
                    "Proposed patch, or a file list of {path, patch} objects. "
                    f"A string is truncated at {GATE.doc_units} chars."
                ),
                "anyOf": [
                    {"type": "string", "minLength": 1},
                    {
                        "type": "array",
                        "minItems": 1,
                        "items": {
                            "type": "object",
                            "properties": {
                                "path": {"type": "string", "minLength": 1},
                                "patch": {"type": "string", "minLength": 1},
                            },
                            "required": ["path", "patch"],
                            "additionalProperties": False,
                        },
                    },
                ],
            },
            "claims": {
                "type": "array",
                "items": {"type": "string", "minLength": 1},
                "minItems": GATE.claims_min,
                "maxItems": GATE.claims_max,
                "description": "Completion claims to check against evidence: each one a plain string, truncated at "
                f"{GATE.claim_units} chars. Up to {GATE.claims_max} per call.",
            },
            "evidence": EVIDENCE_SCHEMA,
            "tests": {
                "type": "string",
                "description": "Reported test output for the patch review. Truncated at the same cap.",
            },
            "tests_format": {"type": "string", "description": "text, junit, or tap. Omitted text is self-reported."},
            "tests_sha256": {
                "type": "string",
                "description": "Hash of a tests log a reader hashed. The server does not hash caller text.",
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


CLAIM_SUPPORT = (
    "request and claims are assertions, not evidence. The evidence items include the proposed diff and the "
    "reported tests when those were supplied. A truncated diff or test log is not support for the missing part."
)
"""Replaces the sentence that forbade using diff and tests (ADR-0063)."""


def claim_question(index: int) -> ChoiceQuestion:
    return ChoiceQuestion(
        f"Does the evidence support claims[{index}]? Judge only from the provided evidence, not world knowledge. "
        + CLAIM_SUPPORT
        + ROLE_RULE
        + ANTI_INJECTION,
        CLAIM_CRITERIA,
    )


def gate_source_question(index: int, evidence_ids: list[str]) -> ChoiceQuestion:
    criteria: dict[str, str | None] = {item_id: None for item_id in evidence_ids}
    criteria["none"] = NO_SOURCE
    return ChoiceQuestion(f"Which evidence item does claims[{index}] rest on?" + VERIFY_SUFFIX, criteria)


def _refused(error: str) -> ToolResult:
    return ToolResult({"tool": "jev_gate", "error": error}, is_error=True)


def _sent_evidence_item(item: dict[str, object], ledger: CapLedger) -> dict[str, object]:
    sent: dict[str, object] = {
        "id": item["id"],
        "text": ledger.text(str(item["text"]), GATE.doc_units, "context"),
    }
    if item.get("kind"):
        sent["kind"] = item["kind"]
    if item.get("role"):
        sent["role"] = item["role"]
    return sent


def _file_patches(diff: object) -> list[dict[str, str]]:
    if not isinstance(diff, list):
        raise ToolError("diff file list was not a list")
    files: list[dict[str, str]] = []
    for raw in cast(list[object], diff):
        if not isinstance(raw, dict):
            raise ToolError("diff file list item was not an object")
        record = cast(dict[str, object], raw)
        files.append({"path": str(record["path"]), "patch": str(record["patch"])})
    return files


def _implicit_evidence(diff: str | None, tests: str | None) -> list[dict[str, object]]:
    items: list[dict[str, object]] = []
    if diff:
        items.append({"id": "diff", "text": diff, "kind": "diff", "role": "after"})
    if tests:
        items.append({"id": "tests", "text": tests, "kind": "tool_output", "role": "current"})
    return items


async def handle(args: dict[str, Any], runtime: Runtime) -> ToolResult:
    settings = review_settings(args)
    if isinstance(args.get("diff"), list):
        return await _handle_split_diff(args, runtime, settings)
    thresholds = settings.thresholds
    raw_evidence = evidence_items(args["evidence"])
    evidence = ensure_unique_ids(raw_evidence, "evidence").items
    # Bound the request before any model call: item count, then aggregate size.
    if exceeds(len(evidence), GATE.evidence_items):
        return _refused(gate_evidence_items_error(GATE.evidence_items))
    if exceeds(sum(length(str(item["text"])) for item in evidence), GATE.aggregate_evidence_units):
        return _refused(gate_evidence_aggregate_error(GATE.aggregate_evidence_units))

    ledger = CapLedger()
    docs = review_docs(args, ledger, GATE.doc_units)
    claims: list[str] = args["claims"]
    sent_claims = [ledger.text(claim, GATE.claim_units, "context") for claim in claims]
    sent_evidence = [_sent_evidence_item(item, ledger) for item in evidence]
    implicit = _implicit_evidence(docs.diff, docs.tests)
    asked_evidence = ensure_unique_ids([*sent_evidence, *implicit], "evidence").items
    renamed = caller_renames(raw_evidence, asked_evidence)
    truncated = ledger.context_cut

    state = {
        "purpose": "Review the proposed diff against the request, then check each completion claim against the "
        "evidence only.",
        "request": docs.request,
        "diff": docs.diff,
        "tests": docs.tests,
        "claims": sent_claims,
        "evidence": asked_evidence,
    }
    # Review questions carry extra framing so claims cannot read as proof; claims use evidence only.
    questions = review_questions(REVIEW_FRAMING)
    evidence_ids = [str(item["id"]) for item in asked_evidence]
    for index in range(len(claims)):
        questions[f"claim_{index}"] = claim_question(index)
        if len(asked_evidence) > 1:
            questions[f"source_{index}"] = gate_source_question(index, evidence_ids)
    evaluation = await runtime.ask(state, questions)
    answers = evaluation.answers

    review = project_review(answers, settings, truncated)
    if docs.tests and not args.get("tests_sha256"):
        review.payload["tests_weight"] = "self_reported"

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
            closed_row: dict[str, object] = {
                "claim": claim,
                "verdict": None,
                "confidence": None,
                "probabilities": None,
                "action": closed,
                "status": "invalid_response",
            }
            closed_row.update(claim_extras(closed_row, asked_evidence, claim_id=f"claim{index}", supporting=None))
            results.append(closed_row)
            continue
        verdict = CLAIM_VERDICTS[answer.choice]
        judgments.append(ClaimJudgment(verdict, answer.confidence))
        source = validate_choice(answers.get(f"source_{index}"), [*evidence_ids, "none"])
        support = source.choice if source is not None and source.choice != "none" else None
        action = require_complete_context(
            claim_action(verdict, answer.confidence, thresholds.auto_accept, thresholds.review_at), truncated
        )
        if note_blocks_auto(action, support, asked_evidence):
            action = "review"
        claim_actions.append(action)
        row: dict[str, object] = {
            "claim": claim,
            "verdict": verdict,
            "confidence": answer.confidence,
            "probabilities": answer.probabilities,
            "action": action,
        }
        row.update(claim_extras(row, asked_evidence, claim_id=f"claim{index}", supporting=support))
        results.append(row)

    verification_action = worst_action(claim_actions)
    verification = {
        "action": verification_action,
        "summary": {
            "verified": sum(1 for r in results if r["verdict"] == "verified"),
            "contradicted": sum(1 for r in results if r["verdict"] == "contradicted"),
            "unsupported": sum(1 for r in results if r["verdict"] == "unsupported"),
            "needs_review": sum(1 for r in results if r["action"] != "auto"),
            "invalid_response": sum(1 for r in results if r.get("status") == "invalid_response"),
            **summary_extras(results),
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
        caller_note=any(
            row.get("supporting_evidence") and note_blocks_auto("auto", row.get("supporting_evidence"), asked_evidence)
            for row in results
        ),
    )
    return ToolResult(
        frame(
            "jev_gate",
            evaluation,
            {
                "truncated": truncated,
                "action": action,
                "reason_codes": reason_codes,
                "next_checks": next_checks_for(reason_codes),
                "review": review.payload,
                "verification": verification,
                **renamed_ids_field(renamed),
            },
        ),
        action=action,
        truncated=ledger.scopes,
    )


async def _handle_split_diff(args: dict[str, Any], runtime: Runtime, settings: object) -> ToolResult:
    """Per-file review when the joined diff exceeds the document cap (ADR-0066).

    A file over the cap is unreviewed. The call never returns auto while any file is unreviewed.
    """
    del settings
    files = _file_patches(args["diff"])
    total = sum(length(item["patch"]) for item in files)
    if exceeds(total, GATE.aggregate_evidence_units):
        return _refused(gate_evidence_aggregate_error(GATE.aggregate_evidence_units))
    fitting = [item for item in files if length(str(item["patch"])) <= GATE.doc_units]
    unreviewed = [str(item["path"]) for item in files if length(str(item["patch"])) > GATE.doc_units]
    if not fitting:
        return ToolResult(
            {
                "tool": "jev_gate",
                "action": "review",
                "partial": True,
                "unreviewed_files": unreviewed,
                "reason_codes": ["incomplete_context"],
                "next_checks": next_checks_for(["incomplete_context"]),
            }
        )
    # Each fitting file is under the cap. Do not join them back into a string that would be cut.
    actions: list[Action] = []
    payload: dict[str, object] = {}
    for item in fitting:
        reviewed = await handle({**args, "diff": item["patch"]}, runtime)
        payload = dict(reviewed.payload)
        action = payload.get("action")
        if action in ("auto", "review", "escalate"):
            actions.append(action)
    if actions:
        payload["action"] = worst_action(actions)
    payload["partial"] = bool(unreviewed)
    payload["unreviewed_files"] = unreviewed
    if unreviewed and payload.get("action") == "auto":
        payload["action"] = "review"
    headline = payload.get("action")
    return ToolResult(payload, action=headline if headline in ("auto", "review", "escalate") else "review")


TOOL = JevTool(DEFINITION, handle, {"evidence": EVIDENCE_NOT_EMPTY})
