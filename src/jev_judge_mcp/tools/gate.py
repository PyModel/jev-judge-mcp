"""jev_gate: review a patch and verify completion claims in one call (`index.ts:1342-1495`)."""

from dataclasses import dataclass
from typing import Any

from jev_judge_mcp.domain import ChoiceQuestion, Question
from jev_judge_mcp.ids import ensure_unique_ids
from jev_judge_mcp.limits import GATE
from jev_judge_mcp.policy import Action, ClaimJudgment, ClaimVerdict, PolicyThresholds
from jev_judge_mcp.policy.claims import note_blocks_auto
from jev_judge_mcp.providers import Evaluation
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
from jev_judge_mcp.tools.base import JevTool, Runtime, ToolResult, define, frame
from jev_judge_mcp.tools.common import EVIDENCE_SCHEMA, evidence_items, has_non_empty_evidence, normalize_evidence
from jev_judge_mcp.tools.files import combined, file_patches
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
    ReviewHalf,
    ReviewSettings,
    project_review,
    review_docs,
    review_questions,
    review_settings,
)
from jev_judge_mcp.tools.verify import NO_SOURCE, ROLE_RULE, VERIFY_SUFFIX
from jev_judge_mcp.validation.caps import (
    CapLedger,
    CapScope,
    exceeds,
    gate_diff_aggregate_error,
    gate_evidence_aggregate_error,
    gate_evidence_items_error,
)

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


def _implicit_evidence(diff: str | None, tests: str | None) -> list[dict[str, object]]:
    items: list[dict[str, object]] = []
    if diff:
        items.append({"id": "diff", "text": diff, "kind": "diff", "role": "after"})
    if tests:
        items.append({"id": "tests", "text": tests, "kind": "tool_output", "role": "current"})
    return items


@dataclass(frozen=True, slots=True)
class GateAsk:
    """One string-diff gate evaluation: the payload body, the headline action, and the ask itself."""

    body: dict[str, object]
    action: Action
    evaluation: Evaluation
    truncated: frozenset[CapScope]


@dataclass(frozen=True, slots=True)
class VerificationHalf:
    """The claim half of a gate: canonical rows, the worst claim action, and their judgments."""

    payload: dict[str, object]
    action: Action
    judgments: list[ClaimJudgment | None]
    caller_note: bool


async def handle(args: dict[str, Any], runtime: Runtime) -> ToolResult:
    settings = review_settings(args)
    # The evidence budgets are evidence-driven and identical for every per-file call, so they are
    # refused once here, before the split: a file list over budget is an isError like a string is.
    raw_evidence = evidence_items(args["evidence"])
    evidence = ensure_unique_ids(raw_evidence, "evidence").items
    if exceeds(len(evidence), GATE.evidence_items):
        return _refused(gate_evidence_items_error(GATE.evidence_items))
    if exceeds(sum(length(str(item["text"])) for item in evidence), GATE.aggregate_evidence_units):
        return _refused(gate_evidence_aggregate_error(GATE.aggregate_evidence_units))
    if isinstance(args.get("diff"), list):
        return await _handle_file_list(args, runtime, settings, evidence, raw_evidence)
    ask = await _ask_gate(args, runtime, settings, evidence, raw_evidence)
    return ToolResult(frame("jev_gate", ask.evaluation, ask.body), action=ask.action, truncated=ask.truncated)


async def _ask_gate(
    args: dict[str, Any],
    runtime: Runtime,
    settings: ReviewSettings,
    evidence: list[dict[str, object]],
    raw_evidence: list[dict[str, object]],
) -> GateAsk:
    """One string-diff gate ask: the review rubric and every claim and source question together."""
    thresholds = settings.thresholds
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

    verification = _verify_claims(answers, claims, asked_evidence, evidence_ids, thresholds, truncated)
    verification_action = verification.action
    action = worst_action([review.action, verification_action])
    reason_codes = gate_reason_codes(
        truncated=truncated,
        review_action=review.action,
        review_invalid=review.invalid,
        claims=verification.judgments,
        action=action,
        thresholds=thresholds,
        caller_note=verification.caller_note,
    )
    body: dict[str, object] = {
        "truncated": truncated,
        "action": action,
        "reason_codes": reason_codes,
        "next_checks": next_checks_for(reason_codes),
        "review": review.payload,
        "verification": verification.payload,
        **renamed_ids_field(renamed),
    }
    return GateAsk(body, action, evaluation, ledger.scopes)


def _verify_claims(
    answers: dict[str, object],
    claims: list[str],
    asked_evidence: list[dict[str, object]],
    evidence_ids: list[str],
    thresholds: PolicyThresholds,
    truncated: bool,
) -> VerificationHalf:
    """`claim` and `source` answers to one canonical verification block, for any ask shape."""
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
    verification: dict[str, object] = {
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
    caller_note = any(
        row.get("supporting_evidence") and note_blocks_auto("auto", row.get("supporting_evidence"), asked_evidence)
        for row in results
    )
    return VerificationHalf(verification, verification_action, judgments, caller_note)


async def _handle_file_list(
    args: dict[str, Any],
    runtime: Runtime,
    settings: ReviewSettings,
    evidence: list[dict[str, object]],
    raw_evidence: list[dict[str, object]],
) -> ToolResult:
    """Per-file review plus one claims verification (ADR-0066 and its amendment).

    A file over the cap is unreviewed. The call never returns auto while any file is unreviewed.
    Each fitting file is asked the review rubric alone; the claims and source questions are asked
    once, after the files, with the evidence sent once, so the verification rows are canonical
    and the action is the worst of what the rows and the file reviews actually say.
    """
    files = file_patches(args["diff"])
    total = sum(length(item["patch"]) for item in files)
    if exceeds(total, GATE.aggregate_evidence_units):
        return _refused(gate_diff_aggregate_error(GATE.aggregate_evidence_units))
    fitting = [item for item in files if length(str(item["patch"])) <= GATE.doc_units]
    unreviewed = [str(item["path"]) for item in files if length(str(item["patch"])) > GATE.doc_units]
    if not fitting:
        unasked: dict[str, object] = {
            "action": "review",
            "partial": True,
            "unreviewed_files": unreviewed,
            "reason_codes": ["incomplete_context"],
            "next_checks": next_checks_for(["incomplete_context"]),
        }
        return ToolResult(frame("jev_gate", None, unasked, model=runtime.model), action="review")
    # Each fitting file is under the cap. Do not join them back into a string that would be cut.
    halves: list[ReviewHalf] = []
    evaluations: list[Evaluation] = []
    reviewed_paths: list[str] = []
    unhashed_tests = False
    truncated = False
    scopes: frozenset[CapScope] = frozenset()
    for item in fitting:
        file_args = {**args, "diff": item["patch"]}
        ledger = CapLedger()
        docs = review_docs(file_args, ledger, GATE.doc_units)
        # No claims ride with a file review: the claim questions get their own ask, on the evidence.
        evaluation = await runtime.ask(
            {
                "purpose": (
                    "Review one file of the proposed multi-file patch against the request; tests is reported "
                    "test output. Completion claims are checked against the evidence in a separate step, "
                    "and this review is not evidence for them."
                ),
                "request": docs.request,
                "diff": docs.diff,
                "tests": docs.tests,
            },
            review_questions(),
        )
        halves.append(project_review(evaluation.answers, settings, ledger.context_cut))
        evaluations.append(evaluation)
        reviewed_paths.append(str(item["path"]))
        if docs.tests and not args.get("tests_sha256"):
            unhashed_tests = True
        truncated = truncated or ledger.context_cut
        scopes |= ledger.scopes
    # One verification ask: every claim and source question once, the evidence sent once. The
    # fitting files join the evidence as one implicit diff item per file, so a claim can rest on
    # a file's patch the same way a claim rests on the string diff.
    ledger = CapLedger()
    request = ledger.text(args["request"], GATE.doc_units, "context")
    tests_arg: str | None = args.get("tests")
    tests = ledger.text(tests_arg, GATE.doc_units, "context") if tests_arg else None
    claims: list[str] = args["claims"]
    sent_claims = [ledger.text(claim, GATE.claim_units, "context") for claim in claims]
    sent_evidence = [_sent_evidence_item(item, ledger) for item in evidence]
    implicit = [
        *({"id": f"diff:{item['path']}", "text": item["patch"], "kind": "diff", "role": "after"} for item in fitting),
        *_implicit_evidence(None, tests),
    ]
    asked_evidence = ensure_unique_ids([*sent_evidence, *implicit], "evidence").items
    renamed = caller_renames(raw_evidence, asked_evidence)
    evidence_ids = [str(item["id"]) for item in asked_evidence]
    questions: dict[str, Question] = {}
    for index in range(len(claims)):
        questions[f"claim_{index}"] = claim_question(index)
        if len(asked_evidence) > 1:
            questions[f"source_{index}"] = gate_source_question(index, evidence_ids)
    evaluation = await runtime.ask(
        {
            "purpose": (
                "Check each completion claim of the proposed multi-file patch against the evidence only. The "
                "request and the claims are assertions to check, never evidence."
            ),
            "request": request,
            "claims": sent_claims,
            "evidence": asked_evidence,
        },
        questions,
    )
    verification = _verify_claims(
        evaluation.answers, claims, asked_evidence, evidence_ids, settings.thresholds, ledger.context_cut
    )
    evaluations.append(evaluation)
    truncated = truncated or ledger.context_cut
    scopes |= ledger.scopes

    review_action = worst_action([half.action for half in halves])
    review_payload = dict(halves[0].payload)
    review_payload["action"] = review_action
    review_payload["score_file"] = reviewed_paths[0]
    review_payload["reviewed_files"] = reviewed_paths
    if unhashed_tests:
        review_payload["tests_weight"] = "self_reported"
    action = worst_action([review_action, verification.action])
    if unreviewed and action == "auto":
        action = "review"
    # An unreviewed file is incomplete context even when nothing was cut: the clamp to review
    # must name why. The payload's own `truncated` field below stays cut-honest (false).
    reason_codes = gate_reason_codes(
        truncated=truncated or bool(unreviewed),
        review_action=review_action,
        review_invalid=any(half.invalid for half in halves),
        claims=verification.judgments,
        action=action,
        thresholds=settings.thresholds,
        caller_note=verification.caller_note,
    )
    payload: dict[str, object] = {
        "truncated": truncated,
        "action": action,
        "reason_codes": reason_codes,
        "next_checks": next_checks_for(reason_codes),
        "review": review_payload,
        "verification": verification.payload,
        **renamed_ids_field(renamed),
        "partial": bool(unreviewed),
        "unreviewed_files": unreviewed,
    }
    return ToolResult(frame("jev_gate", combined(evaluations), payload), action=action, truncated=scopes)


TOOL = JevTool(DEFINITION, handle, {"evidence": EVIDENCE_NOT_EMPTY})
