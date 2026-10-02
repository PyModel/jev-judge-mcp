"""jev_review: score a proposed patch before the task is called done (`index.ts:1130-1340`).

The review half (questions, thresholds, documents, projection) lives in `review_half.py` and is
shared with jev_gate; the per-file pass over a `[{path, patch}]` diff lives in `files.py` (ADR-0066).
Both are re-exported here for their callers.
"""

from typing import Any

from jev_judge_mcp.limits import REVIEW
from jev_judge_mcp.responses import SCORE_SCALE
from jev_judge_mcp.tools.base import JevTool, Runtime, ToolError, ToolResult, define, frame
from jev_judge_mcp.tools.files import FileListReview, combined, file_list_refusal, file_patches, review_file_list
from jev_judge_mcp.tools.observed import (
    file_list_action,
)
from jev_judge_mcp.tools.review_half import (
    ANTI_INJECTION,
    RUBRICS,
    ReviewDocs,
    ReviewHalf,
    ReviewSettings,
    project_review,
    review_docs,
    review_questions,
    review_settings,
)
from jev_judge_mcp.validation.caps import CapLedger

PURPOSE = "Review the proposed diff against the request; tests is reported test output."
"""The review state's framing, for the string diff and for every file of a file list alike."""

DEFINITION = define(
    "jev_review",
    "Review a proposed patch",
    "Score a proposed diff against the request with TypeSafe Jev before the task is called done. Returns 0..2 "
    "rubric scores for correctness, spec match, test gap, and blast radius (the last two lower the weighted "
    "composite), a safe_to_apply probability, and an auto | review | escalate action. Auto requires safe_to_apply "
    "and min score confidence at auto_accept and the composite at composite_floor; truncated or malformed input "
    "never returns auto. Does not apply the patch or run tests. Use jev_gate to also verify completion claims "
    "against evidence in the same call.",
    {
        "type": "object",
        "properties": {
            "request": {
                "type": "string",
                "minLength": 1,
                "description": "What the user asked for; this frames the review, it is not proof of anything.",
            },
            "diff": {
                "description": (
                    "Proposed patch, file excerpt, change summary, or a file list of {path, patch} objects. "
                    f"A string is truncated at {REVIEW.doc_units} chars."
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
            "tests": {"type": "string", "description": "Reported test output, if any. Truncated at the same cap."},
            "tests_format": {"type": "string", "description": "text, junit, or tap. Omitted text is self-reported."},
            "tests_sha256": {
                "type": "string",
                "description": "Hash of a tests log the caller read. Unhashed text is self-reported.",
            },
            "auto_accept": {
                "type": "number",
                "minimum": 0,
                "maximum": 1,
                "description": "safe_to_apply and min score confidence at or above this may stand automatically. "
                "Default 0.8.",
            },
            "review_at": {
                "type": "number",
                "minimum": 0,
                "maximum": 1,
                "description": "Min score confidence or safe_to_apply below this escalates. Must be <= auto_accept. "
                "Default min(0.5, auto_accept).",
            },
            "composite_floor": {
                "type": "number",
                "minimum": 0,
                "maximum": 1,
                "description": "Weighted composite at or above this is required for auto. Default 0.7.",
            },
        },
        "required": ["request", "diff"],
        "additionalProperties": False,
    },
)


async def handle(args: dict[str, Any], runtime: Runtime) -> ToolResult:
    settings = review_settings(args)
    if isinstance(args.get("diff"), list):
        return await _handle_file_list(args, runtime, settings)
    ledger = CapLedger()
    docs = review_docs(args, ledger, REVIEW.doc_units)
    truncated = ledger.context_cut

    state = {
        "purpose": PURPOSE,
        "request": docs.request,
        "diff": docs.diff,
        "tests": docs.tests,
    }
    evaluation = await runtime.ask(state, review_questions())
    review = project_review(evaluation.answers, settings, truncated)
    if docs.tests and not args.get("tests_sha256"):
        review.payload["tests_weight"] = "self_reported"
    return ToolResult(
        frame(
            "jev_review",
            evaluation,
            {
                "truncated": truncated,
                **review.payload,
            },
        ),
        action=review.action,
        truncated=ledger.scopes,
    )


async def _handle_file_list(args: dict[str, Any], runtime: Runtime, settings: ReviewSettings) -> ToolResult:
    """Review each file under the document cap. Unreviewed files block auto (ADR-0066)."""
    files = file_patches(args["diff"])
    refusal = file_list_refusal(files)
    if refusal is not None:
        raise ToolError(refusal, code="input_too_large")
    reviewed = await review_file_list(files, args, runtime, settings, purpose=PURPOSE, doc_units=REVIEW.doc_units)
    if not reviewed.halves:
        payload: dict[str, object] = {
            "action": "review",
            "partial": True,
            "unreviewed_files": reviewed.unreviewed,
            "score_scale": list(SCORE_SCALE),
        }
        return ToolResult(frame("jev_review", None, payload, model=runtime.model), action="review")
    action = file_list_action([half.action for half in reviewed.halves], bool(reviewed.unreviewed))
    # `truncated` reports cuts, as the string path's does; an unreviewed file is `partial`, not a cut.
    payload = {"truncated": reviewed.truncated, **reviewed.review_half(action)}
    payload["partial"] = bool(reviewed.unreviewed)
    payload["unreviewed_files"] = reviewed.unreviewed
    if reviewed.unhashed_tests:
        payload["tests_weight"] = "self_reported"
    return ToolResult(
        frame("jev_review", combined(reviewed.evaluations), payload), action=action, truncated=reviewed.scopes
    )


TOOL = JevTool(DEFINITION, handle)

__all__ = [
    "ANTI_INJECTION",
    "DEFINITION",
    "PURPOSE",
    "RUBRICS",
    "TOOL",
    "FileListReview",
    "ReviewDocs",
    "ReviewHalf",
    "ReviewSettings",
    "handle",
    "project_review",
    "review_docs",
    "review_questions",
    "review_settings",
]
