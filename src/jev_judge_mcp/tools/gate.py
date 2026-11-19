"""jev_gate: review a patch and verify completion claims in one call (`index.ts:1342-1495`)."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from jev_judge_mcp.domain import ChoiceQuestion, Question
from jev_judge_mcp.gate_budget import SPLIT_GUIDANCE, GateBudgetError, check_gate_budget
from jev_judge_mcp.gate_diagnostics import explain_gate, input_cuts
from jev_judge_mcp.ids import ensure_unique_ids
from jev_judge_mcp.limits import GATE
from jev_judge_mcp.policy import Action, ClaimJudgment, ClaimVerdict, PolicyThresholds
from jev_judge_mcp.policy.claims import note_blocks_auto
from jev_judge_mcp.providers import Evaluation, ProviderError
from jev_judge_mcp.responses import (
    caller_renames,
    claim_extras,
    diff_shape,
    next_checks_for,
    renamed_ids_field,
    review_saw_tests,
    summary_extras,
)
from jev_judge_mcp.serialize import js_number_to_locale_string_en_us
from jev_judge_mcp.text import length
from jev_judge_mcp.tools.arguments import Refinement
from jev_judge_mcp.tools.base import JevTool, Runtime, ToolResult, define, frame
from jev_judge_mcp.tools.common import EVIDENCE_SCHEMA, evidence_items, has_non_empty_evidence, normalize_evidence
from jev_judge_mcp.tools.files import combined, file_list_refusal, file_patches, review_file_list
from jev_judge_mcp.tools.gate_evidence import Citations, add_citations, contradicting_line, grounded_review
from jev_judge_mcp.tools.observed import (
    fail_closed,
    file_list_action,
    gate_claim_action,
    gate_reason_codes,
    validate_choice,
    worst_action,
)
from jev_judge_mcp.tools.review_half import (
    ANTI_INJECTION,
    ReviewDocs,
    ReviewSettings,
    project_review,
    review_questions,
    review_settings,
)
from jev_judge_mcp.tools.verify import NO_SOURCE, ROLE_RULE, VERIFY_SUFFIX
from jev_judge_mcp.validation.caps import (
    exceeds,
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
    "require review. Escalation identifies a cited defect, contradiction or safety refusal. "
    "See rollup and next_checks. The request and "
    "claims are assertions to check, never proof. Pass the real git diff as diff, whole or as a {path, patch} file "
    "list, and raw test output in tests. File reviews include bounded caller evidence and sibling patches. "
    "review.context_omissions lists optional inputs left out. An excerpt, "
    "paraphrase, or path is judged as written and "
    "scores low. The string diff and tests are sent twice. Oversized inputs are refused before review. "
    "Split by file, hunk or claim. Evidence "
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
                    "The real unified diff (git diff output), or a file list of {path, patch} objects, one per "
                    f"file. The document limit is {GATE.doc_units} UTF-16 units. An excerpt or summary is reviewed as "
                    "written and scores low."
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
                "description": "Completion claims to check against evidence: each one a plain string of at most "
                f"{GATE.claim_units} chars. Up to {GATE.claims_max} per call.",
            },
            "evidence": EVIDENCE_SCHEMA,
            "tests": {
                "type": "string",
                "description": "Reported test output for the patch review. Subject to the document and request limits.",
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
                "description": "Low-score diagnostic threshold. An uncited concern requires review. Must be <= "
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
    """A budget refusal. The code is set here, not recovered from the sentence (ADR-0062)."""
    return ToolResult({"tool": "jev_gate", "error": error}, is_error=True, error_code="input_too_large")


def _sent_evidence_item(item: dict[str, object]) -> dict[str, object]:
    sent: dict[str, object] = {
        "id": item["id"],
        "text": item["text"],
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


@dataclass(frozen=True, slots=True)
class VerificationHalf:
    """The claim half of a gate: canonical rows, the worst claim action, and their judgments."""

    payload: dict[str, object]
    action: Action
    judgments: list[ClaimJudgment | None]
    caller_note: bool


async def handle(args: dict[str, Any], runtime: Runtime, *, withheld: Sequence[dict[str, str]] = ()) -> ToolResult:
    settings = review_settings(args)
    # The evidence budgets are evidence-driven and identical for every per-file call, so they are
    # refused once here, before the split: a file list over budget is an isError like a string is.
    raw_evidence = evidence_items(args["evidence"])
    evidence = ensure_unique_ids(raw_evidence, "evidence").items
    if exceeds(len(evidence), GATE.evidence_items):
        return _refused(gate_evidence_items_error(GATE.evidence_items))
    if exceeds(sum(length(str(item["text"])) for item in evidence), GATE.aggregate_evidence_units):
        return _refused(gate_evidence_aggregate_error(GATE.aggregate_evidence_units))
    if cuts := input_cuts(args, evidence):
        cut = cuts[0]
        return _refused(f"{cut['field']} exceeds {cut['threshold']} UTF-16 units. {SPLIT_GUIDANCE}")
    try:
        if isinstance(args.get("diff"), list):
            return await _handle_file_list(args, runtime, settings, evidence, raw_evidence, withheld)
        ask = await _ask_gate(args, runtime, settings, evidence, raw_evidence)
    except GateBudgetError as error:
        return _refused(str(error))
    except ProviderError as error:
        if error.status == 400 and error.error_type == "max_tokens_exceeded":
            return _refused(
                f"Provider context limit reached (32k tokens per state and question, 64k total). {SPLIT_GUIDANCE}"
            )
        raise
    return ToolResult(frame("jev_gate", ask.evaluation, ask.body), action=ask.action)


async def _ask_gate(
    args: dict[str, Any],
    runtime: Runtime,
    settings: ReviewSettings,
    evidence: list[dict[str, object]],
    raw_evidence: list[dict[str, object]],
) -> GateAsk:
    """One string-diff gate ask: the review rubric and every claim and source question together."""
    thresholds = settings.thresholds
    docs = ReviewDocs(args["request"], args["diff"], args.get("tests") or None)
    claims: list[str] = args["claims"]
    sent_evidence = [_sent_evidence_item(item) for item in evidence]
    implicit = _implicit_evidence(docs.diff, docs.tests)
    asked_evidence = ensure_unique_ids([*sent_evidence, *implicit], "evidence").items
    renamed = caller_renames(raw_evidence, asked_evidence)

    state = {
        "purpose": "Review the proposed diff against the request, then check each completion claim against the "
        "evidence only.",
        "request": docs.request,
        "diff": docs.diff,
        "tests": docs.tests,
        "claims": claims,
        "evidence": asked_evidence,
    }
    # Review questions carry extra framing so claims cannot read as proof; claims use evidence only.
    questions = review_questions(REVIEW_FRAMING)
    evidence_ids = [str(item["id"]) for item in asked_evidence]
    for index in range(len(claims)):
        questions[f"claim_{index}"] = claim_question(index)
        if len(asked_evidence) > 1:
            questions[f"source_{index}"] = gate_source_question(index, evidence_ids)
    check_gate_budget(state, questions)
    patch_lines = add_citations(state, questions, [_implicit_evidence(docs.diff, None)[0]])
    counter_lines = add_citations(state, questions, asked_evidence, claim_count=len(claims))
    evaluation = await runtime.ask(state, questions)
    answers = evaluation.answers

    review = project_review(answers, settings, False)
    review, blockers = grounded_review(review, answers, patch_lines, thresholds.auto_accept)
    if docs.tests and not args.get("tests_sha256"):
        review.payload["tests_weight"] = "self_reported"

    verification = _verify_claims(answers, claims, asked_evidence, evidence_ids, thresholds, counter_lines)
    verification_action = verification.action
    action = worst_action([review.action, verification_action])
    reason_codes = gate_reason_codes(
        truncated=False,
        review_action=review.action,
        review_invalid=review.invalid,
        claims=verification.judgments,
        action=action,
        thresholds=thresholds,
        caller_note=verification.caller_note,
    )
    body: dict[str, object] = {
        "truncated": False,
        "action": action,
        "reason_codes": reason_codes,
        # A text or trimmed diff that did not stand gets its hint: computed from the argument, not judged.
        "next_checks": next_checks_for(
            reason_codes, diff_shape=diff_shape(args["diff"]), review_saw_tests=review_saw_tests(args)
        ),
        "review": review.payload,
        "verification": verification.payload,
        **renamed_ids_field(renamed),
    }
    explain_gate(body, blockers)
    return GateAsk(body, action, evaluation)


def _verify_claims(
    answers: dict[str, object],
    claims: list[str],
    asked_evidence: list[dict[str, object]],
    evidence_ids: list[str],
    thresholds: PolicyThresholds,
    counter_lines: Citations,
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
        reported = verdict
        line = (
            contradicting_line(
                claim, counter_lines.select(answers.get(f"counterexample_{index}"), thresholds.auto_accept)
            )
            if reported == "contradicted"
            else None
        )
        if verdict == "contradicted" and (
            line is None or answer.confidence is None or answer.confidence < thresholds.auto_accept
        ):
            verdict = "unsupported"
        judgments.append(ClaimJudgment(verdict, answer.confidence))
        # Asked only when more than one evidence item was sent (the same condition as the ask).
        # Validating the missing answer would count a fail-closed that never happened.
        source = (
            validate_choice(answers.get(f"source_{index}"), [*evidence_ids, "none"])
            if len(asked_evidence) > 1
            else None
        )
        support = source.choice if source is not None and source.choice != "none" else None
        action = gate_claim_action(
            verdict,
            answer.confidence,
            thresholds,
            truncated=False,
            source=None if source is None else source.choice,
            evidence=asked_evidence,
        )
        if action == "escalate" and verdict != "contradicted":
            action = "review"
        claim_actions.append(action)
        row: dict[str, object] = {
            "claim": claim,
            "verdict": verdict,
            "confidence": answer.confidence,
            "probabilities": answer.probabilities,
            "action": action,
        }
        if reported == "contradicted":
            row["reported_verdict"] = reported
            row["citation_coverage"] = counter_lines.coverage()
            if verdict == "contradicted" and line is not None:
                row["evidence_line"] = line
                support = line["source"]
        row.update(claim_extras(row, asked_evidence, claim_id=f"claim{index}", supporting=support))
        if verdict == "contradicted" and line is not None:
            row["excerpt"] = line["text"]
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


_FILE_REVIEW_PURPOSE = (
    "Review one file of the proposed multi-file patch against the request; tests is reported "
    "test output. Judge the target path and diff using related_files and evidence as context. "
    "Completion claims are checked against the evidence in a separate step, "
    "and this review is not evidence for them."
)
"""The per-file review state's framing: no claims ride with a file review (ADR-0066 amendment)."""


async def _handle_file_list(
    args: dict[str, Any],
    runtime: Runtime,
    settings: ReviewSettings,
    evidence: list[dict[str, object]],
    raw_evidence: list[dict[str, object]],
    withheld: Sequence[dict[str, str]],
) -> ToolResult:
    """Per-file review plus one claims verification (ADR-0066 and its amendment).

    Preflight the complete verification envelope before paying for any file. Each file review
    has its own budget check before fan-out. The claims are verified once over the full evidence.
    """
    files = file_patches(args["diff"])
    refusal = file_list_refusal(files)
    if refusal is not None:
        return _refused(f"{refusal.rstrip('.')}. {SPLIT_GUIDANCE}")
    for index, item in enumerate(files):
        if length(item["patch"]) > GATE.doc_units:
            return _refused(f"diff[{index}].patch exceeds {GATE.doc_units} UTF-16 units. {SPLIT_GUIDANCE}")
    request: str = args["request"]
    tests: str | None = args.get("tests") or None
    claims: list[str] = args["claims"]
    sent_evidence = [_sent_evidence_item(item) for item in evidence]
    implicit = [
        *({"id": f"diff:{item['path']}", "text": item["patch"], "kind": "diff", "role": "after"} for item in files),
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
    state = {
        "purpose": (
            "Check each completion claim of the proposed multi-file patch against the evidence only. The "
            "request and the claims are assertions to check, never evidence."
        ),
        "request": request,
        "claims": claims,
        "evidence": asked_evidence,
    }
    check_gate_budget(state, questions)
    counter_lines = add_citations(state, questions, asked_evidence, claim_count=len(claims))
    reviewed = await review_file_list(
        files,
        args,
        runtime,
        settings,
        purpose=_FILE_REVIEW_PURPOSE,
        doc_units=GATE.doc_units,
        evidence=evidence,
        gate_framing=(
            " Judge the target file's contribution to the request in the context of the supplied changes. "
            "Check cross-file contracts and supporting tests. Credit sibling behavior only where the evidence shows it."
        ),
    )
    evaluation = await runtime.ask(state, questions)
    verification = _verify_claims(
        evaluation.answers, claims, asked_evidence, evidence_ids, settings.thresholds, counter_lines
    )

    review_action = worst_action([half.action for half in reviewed.halves])
    review_payload = reviewed.review_half(review_action)
    if reviewed.unhashed_tests:
        review_payload["tests_weight"] = "self_reported"
    action = file_list_action([review_action, verification.action], bool(withheld))
    reason_codes = gate_reason_codes(
        truncated=bool(withheld),
        review_action=review_action,
        review_invalid=any(half.invalid for half in reviewed.halves),
        claims=verification.judgments,
        action=action,
        thresholds=settings.thresholds,
        caller_note=verification.caller_note,
    )
    payload: dict[str, object] = {
        "truncated": False,
        "action": action,
        "reason_codes": reason_codes,
        "next_checks": next_checks_for(
            reason_codes, diff_shape=diff_shape(args["diff"]), review_saw_tests=review_saw_tests(args)
        ),
        "review": review_payload,
        "verification": verification.payload,
        **renamed_ids_field(renamed),
        "partial": bool(withheld),
        "unreviewed_files": [item["path"] for item in withheld],
    }
    withheld_blockers = [
        {
            **item,
            "field": "diff",
            "code": "file_withheld",
            "next_check": f"Review {item['path']} locally. Its patch was withheld ({item['reason']}).",
        }
        for item in withheld
    ]
    explain_gate(payload, [*reviewed.blockers, *withheld_blockers])
    return ToolResult(frame("jev_gate", combined([*reviewed.evaluations, evaluation]), payload), action=action)


TOOL = JevTool(DEFINITION, handle, {"evidence": EVIDENCE_NOT_EMPTY})
