"""jev_gate: review a patch and verify completion claims in one call (`index.ts:1342-1495`)."""

from collections.abc import Sequence
from dataclasses import dataclass
from shlex import quote
from typing import Any, cast

import anyio

from jev_judge_mcp.domain import ChoiceQuestion, Question
from jev_judge_mcp.gate_budget import fits_budget
from jev_judge_mcp.gate_diagnostics import explain_gate, input_cuts
from jev_judge_mcp.ids import generated_ids
from jev_judge_mcp.limits import GATE
from jev_judge_mcp.policy import Action, ClaimJudgment, ClaimVerdict, PolicyThresholds
from jev_judge_mcp.policy.claims import claim_action
from jev_judge_mcp.providers import Evaluation, ProviderError
from jev_judge_mcp.responses import claim_extras, summary_extras
from jev_judge_mcp.serialize import js_number_to_locale_string_en_us
from jev_judge_mcp.text import length
from jev_judge_mcp.tools.arguments import Refinement
from jev_judge_mcp.tools.base import JevTool, Runtime, ToolResult, code_of, define, frame
from jev_judge_mcp.tools.common import EVIDENCE_SCHEMA, evidence_items, has_non_empty_evidence, identify_evidence
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
from jev_judge_mcp.tools.verify import NO_SOURCE, NO_SOURCE_ID, ROLE_RULE, VERIFY_SUFFIX
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

SPLIT_GUIDANCE = "Split the diff by file or hunk and submit each claim with its supporting evidence."

REVIEW_FRAMING = " Claims are assertions to check, not evidence that the patch is correct or tested."

DEFINITION = define(
    "jev_gate",
    "Gate completion: review a patch and verify claims",
    "Review a proposed patch and verify completion claims against supplied evidence. Auto "
    "only when the patch review is accepted and every claim is verified at or above auto_accept. Unsupported claims "
    "require review. Escalation identifies a cited defect, contradiction or safety refusal. "
    "See rollup and next_checks. The request and "
    "claims are assertions to check, never proof. Pass the real git diff as diff, whole or as a {path, patch} file "
    "list, and raw test output in tests. File reviews include bounded caller evidence and sibling patches. "
    "review.context_omissions lists optional inputs left out. An excerpt, "
    "paraphrase, or path is judged as written and "
    "scores low. The string diff and tests are sent twice. File-list claims use packed groups; "
    "a patch above the packing target is sent alone. On provider context overflow, files in the group "
    "are held for local review. Evidence "
    f"is capped at {GATE.evidence_items} items and {js_number_to_locale_string_en_us(GATE.aggregate_evidence_units)} "
    "UTF-16 units in aggregate, which also bounds the sum of file patches. Does not run tests or apply changes. "
    "Use jev_review for a patch without claims, jev_verify for claims without a patch review.",
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
                    f"file (at most {GATE.files_max}). Patches over {GATE.doc_units:,} UTF-16 units are held "
                    "for local review, as are files in a claims group that hits the provider context limit. "
                    "An excerpt is reviewed as written and scores low."
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
    lambda value: has_non_empty_evidence(evidence_items(value)),
    "jev_gate requires at least one evidence item with non-empty text.",
)
"""The `.refine` on gate's evidence (`index.ts:1370-1372`): checked with the arguments, before any request."""


CLAIM_SUPPORT = (
    "request and claims are assertions, not evidence. The evidence items include the proposed diff and the "
    "reported tests when those were supplied. Use only evidence present in this request."
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
    criteria[NO_SOURCE_ID] = NO_SOURCE
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


def _caller_evidence(evidence: list[dict[str, object]], implicit: list[dict[str, object]]) -> list[dict[str, object]]:
    reserved = {NO_SOURCE_ID, "diff", *(str(item["id"]) for item in implicit)}
    return identify_evidence(evidence, reserved)


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
    source_issues: dict[int, str]
    invalid: set[int]


async def handle(
    args: dict[str, Any], runtime: Runtime, *, withheld: Sequence[dict[str, str]] | None = None
) -> ToolResult:
    settings = review_settings(args)
    # The evidence budgets are evidence-driven and identical for every per-file call, so they are
    # refused once here, before the split: a file list over budget is an isError like a string is.
    evidence = evidence_items(args["evidence"])
    if exceeds(len(evidence), GATE.evidence_items):
        return _refused(gate_evidence_items_error(GATE.evidence_items))
    if exceeds(sum(length(str(item["text"])) for item in evidence), GATE.aggregate_evidence_units):
        return _refused(gate_evidence_aggregate_error(GATE.aggregate_evidence_units))
    if cuts := input_cuts(args, evidence):
        cut = cuts[0]
        return _refused(f"{cut['field']} exceeds {cut['threshold']} UTF-16 units. {SPLIT_GUIDANCE}")
    try:
        if isinstance(args.get("diff"), list):
            return await _handle_file_list(args, runtime, settings, evidence, withheld)
        ask = await _ask_gate(args, runtime, settings, evidence)
    except ProviderError as error:
        if code_of(error) == "input_too_large":
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
) -> GateAsk:
    """One string-diff gate ask: the review rubric and every claim and source question together."""
    thresholds = settings.thresholds
    docs = ReviewDocs(args["request"], args["diff"], args.get("tests") or None)
    claims: list[str] = args["claims"]
    implicit = _implicit_evidence(docs.diff, docs.tests)
    caller_evidence = _caller_evidence(evidence, implicit)
    asked_evidence = [*map(_sent_evidence_item, caller_evidence), *implicit]

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
        questions[f"source_{index}"] = gate_source_question(index, evidence_ids)
    patch_lines = add_citations(state, questions, implicit[:1])
    counter_lines = add_citations(state, questions, asked_evidence, claim_count=len(claims))
    evaluation = await runtime.ask(state, questions)
    answers = evaluation.answers

    review = project_review(answers, settings, False)
    review, blockers = grounded_review(review, answers, patch_lines, thresholds.auto_accept)
    if docs.tests and not runtime.tests_read_from_file:
        review.payload["tests_weight"] = "self_reported"

    verification = _verify_claims(answers, claims, asked_evidence, evidence_ids, thresholds, counter_lines)
    verification_action = verification.action
    action = worst_action([review.action, verification_action])
    reason_codes = gate_reason_codes(
        truncated=False,
        review_action=review.action,
        review_invalid=review.invalid or any(b["code"] == "defect_kind_invalid" for b in blockers),
        source_invalid="source_invalid" in verification.source_issues.values(),
        claims=verification.judgments,
        action=action,
        thresholds=thresholds,
        caller_note="caller_note_only" in verification.source_issues.values(),
    )
    body: dict[str, object] = {
        "truncated": False,
        "action": action,
        "reason_codes": reason_codes,
        "review": review.payload,
        "verification": verification.payload,
    }
    explain_gate(body, blockers, args["diff"], verification.source_issues)
    return GateAsk(body, action, evaluation)


def _verify_claims(
    answers: dict[str, object],
    claims: list[str],
    asked_evidence: list[dict[str, object]],
    evidence_ids: list[str],
    thresholds: PolicyThresholds,
    counter_lines: Citations,
    *,
    overflow: bool = False,
) -> VerificationHalf:
    """`claim` and `source` answers to one canonical verification block, for any ask shape."""
    results: list[dict[str, object]] = []
    judgments: list[ClaimJudgment | None] = []
    claim_actions: list[Action] = []
    source_issues: dict[int, str] = {}
    invalid: set[int] = set()
    for index, claim in enumerate(claims):
        answer = validate_choice(answers.get(f"claim_{index}"), CLAIM_CRITERIA)
        if answer is None:
            invalid.add(index)
            closed = "review" if overflow else fail_closed("gate")
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
        source = validate_choice(answers.get(f"source_{index}"), [*evidence_ids, NO_SOURCE_ID])
        if (
            answer.confidence_kind == "malformed"
            or source is None
            or source.confidence_kind == "malformed"
            or (
                reported == "contradicted"
                and verdict != reported
                and answer.confidence is not None
                and answer.confidence >= thresholds.auto_accept
            )
        ):
            invalid.add(index)
        support = source.choice if source is not None and source.choice != NO_SOURCE_ID else None
        action = gate_claim_action(
            verdict,
            answer.confidence,
            thresholds,
            truncated=False,
            source=None if source is None else source.choice,
            evidence=asked_evidence,
        )
        if claim_action(verdict, answer.confidence, thresholds.auto_accept, thresholds.review_at) == "auto":
            if source is None or source.confidence_kind == "malformed":
                source_issues[index] = "source_invalid"
                action = "review"
            elif action == "review":
                source_issues[index] = "caller_note_only"
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

    return _verification_half(results, judgments, claim_actions, source_issues, invalid, thresholds)


def _verification_half(
    results: list[dict[str, object]],
    judgments: list[ClaimJudgment | None],
    claim_actions: list[Action],
    source_issues: dict[int, str],
    invalid: set[int],
    thresholds: PolicyThresholds,
) -> VerificationHalf:
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
    return VerificationHalf(verification, verification_action, judgments, source_issues, invalid)


_FILE_REVIEW_PURPOSE = (
    "Review one file of the proposed multi-file patch against the request; tests is reported "
    "test output. Judge the target path and diff using related_files and evidence as context. "
    "Completion claims are checked against the evidence in a separate step, "
    "and this review is not evidence for them."
)
"""The per-file review state's framing: no claims ride with a file review (ADR-0066 amendment)."""


_FILE_REVIEW_FRAMING = (
    " Judge the target file's contribution to the request in the context of the supplied changes. "
    "Check cross-file contracts and supporting tests. Credit sibling behavior only where the evidence shows it."
)

_CLAIMS_PURPOSE = (
    "Check each completion claim of the proposed multi-file patch against the evidence only. The "
    "request and the claims are assertions to check, never evidence."
)


@dataclass(frozen=True, slots=True)
class ClaimRequest:
    state: dict[str, object]
    questions: dict[str, Question]
    evidence: list[dict[str, object]]
    evidence_ids: list[str]


def _claim_request(request: str, claims: list[str], evidence: list[dict[str, object]]) -> ClaimRequest:
    ids = [str(item["id"]) for item in evidence]
    questions: dict[str, Question] = {}
    for index in range(len(claims)):
        questions[f"claim_{index}"] = claim_question(index)
        questions[f"source_{index}"] = gate_source_question(index, ids)
    return ClaimRequest(
        {"purpose": _CLAIMS_PURPOSE, "request": request, "claims": claims, "evidence": evidence},
        questions,
        evidence,
        ids,
    )


def _aggregate_claims(groups: list[VerificationHalf], thresholds: PolicyThresholds) -> VerificationHalf:
    if len(groups) == 1:
        return groups[0]
    results: list[dict[str, object]] = []
    judgments: list[ClaimJudgment | None] = []
    actions: list[Action] = []
    issues: dict[int, str] = {}
    invalid = {index for group in groups for index in group.invalid}
    for index in range(len(groups[0].judgments)):
        rows = [cast(list[dict[str, object]], group.payload["results"])[index] for group in groups]
        confirmed = [
            (group, row)
            for group, row in zip(groups, rows, strict=True)
            if isinstance(score := row["confidence"], float | int) and score >= thresholds.auto_accept
        ]
        winner = next(((group, row) for group, row in confirmed if row["verdict"] == "contradicted"), None)
        if winner is None:
            verified = [(group, row) for group, row in confirmed if row["verdict"] == "verified"]
            winner = next(((group, row) for group, row in verified if row["action"] == "auto"), None)
            if winner is None and verified:
                winner = verified[0]
        if winner is None:
            winner = next(
                ((group, row) for group, row in zip(groups, rows, strict=True) if row["verdict"] == "unsupported"),
                None,
            )
        if winner is None:
            row = {
                "claim": rows[0]["claim"],
                "id": rows[0]["id"],
                "verdict": "unsupported",
                "confidence": None,
                "probabilities": None,
                "action": "review",
            }
        else:
            group, selected = winner
            row = dict(selected)
            if (issue := group.source_issues.get(index)) is not None:
                issues[index] = issue
        if index in invalid:
            row.update(action="review", status="invalid_response")
            issues[index] = "source_invalid"
        judgments.append(ClaimJudgment(cast(ClaimVerdict, row["verdict"]), cast(float | None, row["confidence"])))
        results.append(row)
        actions.append(cast(Action, row["action"]))
    return _verification_half(results, judgments, actions, issues, invalid, thresholds)


async def _handle_file_list(
    args: dict[str, Any],
    runtime: Runtime,
    settings: ReviewSettings,
    evidence: list[dict[str, object]],
    withheld: Sequence[dict[str, str]] | None,
) -> ToolResult:
    files = file_patches(args["diff"])
    refusal = file_list_refusal(files)
    if refusal is not None:
        return _refused(f"{refusal.rstrip('.')}. {SPLIT_GUIDANCE}")
    request: str = args["request"]
    tests: str | None = args.get("tests") or None
    claims: list[str] = args["claims"]
    ids = generated_ids(f"diff:{item['path']}" for item in files)
    implicit: list[dict[str, object]] = [
        {"id": identifier, "text": item["patch"], "kind": "diff", "role": "after"}
        for identifier, item in zip(ids, files, strict=True)
    ]
    test_evidence = _implicit_evidence(None, tests)
    caller_evidence = _caller_evidence(evidence, [*implicit, *test_evidence])
    caller = list(map(_sent_evidence_item, caller_evidence))

    def prepare(indices: list[int]) -> ClaimRequest:
        return _claim_request(request, claims, [*caller, *(implicit[i] for i in indices), *test_evidence])

    fitting: list[int] = []
    oversized: set[int] = set()
    bins: list[list[int]] = []
    for index, item in enumerate(files):
        if exceeds(length(item["patch"]), GATE.doc_units):
            oversized.add(index)
            continue
        fitting.append(index)
        for group in bins:
            candidate = prepare([*group, index])
            if fits_budget(candidate.state, candidate.questions):
                group.append(index)
                break
        else:
            bins.append([index])
    bins = bins or [[]]
    prepared = [prepare(group) for group in bins]
    citations = [
        add_citations(group.state, group.questions, group.evidence, claim_count=len(claims)) for group in prepared
    ]
    reviewed = await review_file_list(
        [files[i] for i in fitting],
        args,
        runtime,
        settings,
        purpose=_FILE_REVIEW_PURPOSE,
        doc_units=GATE.doc_units,
        evidence=caller_evidence,
        gate_framing=_FILE_REVIEW_FRAMING,
    )
    evaluated: dict[int, Evaluation] = {}
    failures: dict[int, Exception] = {}

    async def verify(index: int, group: ClaimRequest) -> None:
        try:
            async with runtime.file_fanout:
                evaluated[index] = await runtime.ask(group.state, group.questions)
        except Exception as error:
            if code_of(error) == "input_too_large":
                return
            failures[index] = error
            tasks.cancel_scope.cancel()

    async with anyio.create_task_group() as tasks:
        for index, group in enumerate(prepared):
            tasks.start_soon(verify, index, group)
    if failures:
        raise failures[min(failures)]
    overflow = set(range(len(prepared))) - evaluated.keys()
    held = {file_index for group_index in overflow for file_index in bins[group_index]}
    verification = _aggregate_claims(
        [
            _verify_claims(
                evaluated[i].answers if i in evaluated else {},
                claims,
                group.evidence,
                group.evidence_ids,
                settings.thresholds,
                lines,
                overflow=i in overflow,
            )
            for i, (group, lines) in enumerate(zip(prepared, citations, strict=True))
        ],
        settings.thresholds,
    )

    skipped = [
        *(withheld or ()),
        *({"path": files[i]["path"], "reason": "oversize"} for i in sorted(oversized | held)),
    ]
    review_action = file_list_action([half.action for half in reviewed.halves], bool(held or not fitting))
    review_payload = reviewed.review_half(
        review_action, held_indices=frozenset(index for index, file_index in enumerate(fitting) if file_index in held)
    )
    if tests and withheld is None and not runtime.tests_read_from_file:
        review_payload["tests_weight"] = "self_reported"
    action = file_list_action([review_action, verification.action], bool(skipped))
    reason_codes = gate_reason_codes(
        truncated=bool(skipped),
        review_action=review_action,
        review_invalid=any(half.invalid for half in reviewed.halves)
        or any(b["code"] == "defect_kind_invalid" for b in reviewed.blockers),
        source_invalid="source_invalid" in verification.source_issues.values(),
        claims=verification.judgments,
        action=action,
        thresholds=settings.thresholds,
        caller_note="caller_note_only" in verification.source_issues.values(),
    )
    payload: dict[str, object] = {
        "truncated": False,
        "action": action,
        "reason_codes": reason_codes,
        "review": review_payload,
        "verification": verification.payload,
        "partial": bool(skipped),
        "unreviewed_files": [item["path"] for item in skipped],
    }
    if len(prepared) > 1 or overflow or len(reviewed.evaluations) != len(fitting):
        payload["calls"] = len(fitting) + len(prepared)
    if skipped:
        payload["skipped"] = skipped
    withheld_blockers = [
        {
            **item,
            "field": "diff",
            "code": "file_withheld",
            "next_check": _withheld_check(item["path"], item["reason"]),
        }
        for item in skipped
    ]
    explain_gate(payload, [*reviewed.blockers, *withheld_blockers], args["diff"], verification.source_issues)
    next_checks = cast(list[str], payload["next_checks"])
    if overflow:
        next_checks.append(f"Provider context limit reached. {SPLIT_GUIDANCE}")
    for blocker in withheld_blockers:
        if blocker["reason"] == "untracked" and blocker["next_check"] not in next_checks:
            next_checks.append(blocker["next_check"])
    evaluations = [*reviewed.evaluations, *(evaluated[i] for i in sorted(evaluated))]
    return ToolResult(
        frame(
            "jev_gate",
            combined(evaluations) if evaluations else None,
            payload,
            model=runtime.model,
            provider=runtime.provider_name,
        ),
        action=action,
    )


def _withheld_check(path: str, reason: str) -> str:
    if reason == "untracked":
        literal = f"./{path}" if path.startswith(("-", ":")) else path
        return f"Run git add -N {quote(literal)} and recheck the range."
    if reason == "oversize":
        return f"Review {path} locally or split its patch by hunk and resubmit it to jev_gate."
    return f"Review {path} locally. Its patch was withheld ({reason})."


TOOL = JevTool(DEFINITION, handle, {"evidence": EVIDENCE_NOT_EMPTY})
