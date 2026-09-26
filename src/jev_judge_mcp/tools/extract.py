"""jev_extract: regex finds candidates, Jev picks one, the value is returned verbatim (`index.ts:884-1128`).

Hard invariants: a value is one of its field's candidates or null; a call with no candidates at all
makes no provider request (and so never resolves the provider); an incomplete candidate universe
(capped, or matches skipped as too long) is never `auto` and never a definite `not_found`.
"""

from dataclasses import dataclass
from typing import Any

from jev_judge_mcp.domain import ChoiceQuestion, Question
from jev_judge_mcp.extract.candidates import Refused, find_candidates
from jev_judge_mcp.extract.dialect import to_units
from jev_judge_mcp.limits import EXTRACT
from jev_judge_mcp.policy import (
    DEFAULT_CLASSIFY_AUTO_ACCEPT,
    DEFAULT_MINIMUM_MARGIN,
    ExtractFieldEvidence,
    ExtractJudgment,
)
from jev_judge_mcp.serialize import quote
from jev_judge_mcp.text import length
from jev_judge_mcp.tools.base import JevTool, Runtime, ToolError, ToolResult, caller_actions, define, frame, headline
from jev_judge_mcp.tools.observed import decide_extract_field, fail_closed, validate_extract_choice
from jev_judge_mcp.validation import margin, top_probability
from jev_judge_mcp.validation.caps import CapLedger, candidate_budget_error, exceeds

NONE_OF_THEM = "none_of_them"

DEFINITION = define(
    "jev_extract",
    "Extract fields by regex, Jev picks the right match",
    "Extract structured fields from a document with TypeSafe Jev as the picker, not the generator: your regex finds "
    "candidate substrings in code, Jev chooses which candidate is the field's true value, and the result is "
    "returned verbatim — never model-generated text. Fields with zero regex matches never reach the model "
    "(not_found); if no field has matches, no API call is made. Ambiguous picks are flagged for review. Use for "
    "prices, dates, version numbers, IDs, and anything with a recognizable shape; keep documents bounded.",
    {
        "type": "object",
        "properties": {
            "document": {
                "type": "string",
                "minLength": EXTRACT.document_min,
                "maxLength": EXTRACT.document_max,
                "description": f"The document to extract from. Rejected above {EXTRACT.document_max:,} characters.",
            },
            "fields": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {
                            "type": "string",
                            "pattern": "^[a-z][a-z0-9_-]*$",
                            "maxLength": EXTRACT.field_id_max,
                            "description": "Field name, e.g. 'price' or 'version'.",
                        },
                        "pattern": {
                            "type": "string",
                            "minLength": EXTRACT.pattern_min,
                            "maxLength": EXTRACT.pattern_max,
                            "description": "JavaScript regex source (without delimiters) that matches candidate "
                            "values. Runs in a sandboxed worker with a hard timeout.",
                        },
                        "flags": {
                            "type": "string",
                            "maxLength": EXTRACT.flags_max,
                            "description": "Regex flags (e.g. 'i'). 'g' is always added; non-letters are dropped.",
                        },
                        "description": {
                            "type": "string",
                            "minLength": EXTRACT.description_min,
                            "maxLength": EXTRACT.description_max,
                            "description": "What the field is, so Jev can pick the right candidate among regex "
                            "matches.",
                        },
                    },
                    "required": ["id", "pattern", "description"],
                    "additionalProperties": False,
                },
                "minItems": EXTRACT.fields_min,
                "maxItems": EXTRACT.fields_max,
                "description": f"Fields to extract: each one an object {{id, pattern, description}} with flags "
                f"optional. Up to {EXTRACT.fields_max} per call, all judged in one request.",
            },
            "purpose": {"type": "string", "description": "What the extraction is for; shared across fields."},
            "auto_accept": {
                "type": "number",
                "minimum": 0,
                "maximum": 1,
                "description": "Minimum top probability for auto. Default 0.85.",
            },
            "minimum_margin": {
                "type": "number",
                "minimum": 0,
                "maximum": 1,
                "description": "Minimum winner-to-runner-up gap for auto. Default 0.5.",
            },
        },
        "required": ["document", "fields"],
        "additionalProperties": False,
    },
)


@dataclass(frozen=True, slots=True)
class _Field:
    id: str
    key: str
    pattern: str
    description: str
    candidates: list[str]
    too_long: int
    truncated: bool
    error: str | None

    @property
    def flags(self) -> dict[str, object]:
        return {"candidates_truncated": self.truncated, "matches_skipped_too_long": self.too_long}


async def _match(runtime: Runtime, index: int, raw: dict[str, str], units: str) -> _Field:
    telemetry = runtime.telemetry
    with telemetry.span("regex.extract") as span:
        telemetry.payload(span, "pattern", lambda: raw["pattern"])
        found = await find_candidates(runtime.regex_executor, raw["pattern"], raw.get("flags", ""), units)
        if isinstance(found, Refused):
            span.attributes["outcome"] = found.outcome
            candidates, too_long, truncated, error = [], 0, False, found.reason
        else:
            candidates, too_long, truncated, error = found.candidates, found.too_long, found.truncated, None
            span.attributes.update(
                outcome="ok", candidates=len(candidates), too_long=too_long, candidates_truncated=truncated
            )
    return _Field(raw["id"], f"f{index}", raw["pattern"], raw["description"], candidates, too_long, truncated, error)


def _result(field: _Field, answer: object, auto_accept: float, minimum_margin: float) -> dict[str, object]:
    if field.error is not None:
        return {
            "id": field.id,
            "value": None,
            "status": "invalid_pattern",
            "reason": field.error,
            "candidates_considered": 0,
            **field.flags,
        }
    if not field.candidates:
        decision = decide_extract_field(
            ExtractFieldEvidence(field.too_long, field.truncated, None), threshold=auto_accept, margin=minimum_margin
        )
        return {
            "id": field.id,
            "value": None,
            "status": decision.status,
            "reason": decision.reason,
            "candidates_considered": 0,
            **field.flags,
        }
    keys = [f"c{j}" for j in range(len(field.candidates))] + [NONE_OF_THEM]
    validated = validate_extract_choice(answer, keys)
    considered = len(field.candidates)
    if validated is None:
        assert fail_closed("extract") == "status"
        return {
            "id": field.id,
            "value": None,
            "status": "invalid_response",
            "reason": None,
            "candidates_considered": considered,
            **field.flags,
        }
    gap = margin(validated.probabilities)
    top = top_probability(validated)
    none_matched = validated.choice == NONE_OF_THEM
    decision = decide_extract_field(
        ExtractFieldEvidence(field.too_long, field.truncated, ExtractJudgment(none_matched, top, gap)),
        threshold=auto_accept,
        margin=minimum_margin,
    )
    return {
        "id": field.id,
        "value": None if none_matched else field.candidates[int(validated.choice[1:])],
        "status": decision.status,
        "reason": decision.reason,
        "confidence": validated.confidence,
        "top_probability": top,
        "margin": gap,
        "candidates_considered": considered,
        **field.flags,
    }


async def handle(args: dict[str, Any], runtime: Runtime) -> ToolResult:
    auto_accept: float = args.get("auto_accept", DEFAULT_CLASSIFY_AUTO_ACCEPT)
    minimum_margin: float = args.get("minimum_margin", DEFAULT_MINIMUM_MARGIN)
    # The schema rejects a document over its cap first, so only the candidate universe records a cut.
    ledger = CapLedger()
    document = ledger.text(args["document"], EXTRACT.document_max, "context")
    raw_fields: list[dict[str, str]] = args["fields"]

    seen: set[str] = set()
    for raw in raw_fields:
        if raw["id"] in seen:
            raise ToolError(f"Duplicate field id: {raw['id']}")
        seen.add(raw["id"])

    # Fields run one after another, in caller order (ADR-0012 D5).
    units = to_units(document)
    fields = [await _match(runtime, index, raw, units) for index, raw in enumerate(raw_fields)]

    total = sum(length(candidate) for field in fields for candidate in field.candidates)
    if exceeds(total, EXTRACT.aggregate_candidate_units):
        raise ToolError(
            candidate_budget_error(total, EXTRACT.aggregate_candidate_units, "Tighten the patterns or split the call.")
        )

    # One Choice per field with candidates; the document is sent once, candidates only in their criteria.
    questions: dict[str, Question] = {}
    state_fields: list[dict[str, object]] = []
    for field in fields:
        if field.error is not None or not field.candidates:
            continue
        criteria: dict[str, str] = {f"c{j}": f"Candidate value: {quote(c)}" for j, c in enumerate(field.candidates)}
        criteria[NONE_OF_THEM] = "None of the candidates is the value this field asks for"
        questions[field.key] = ChoiceQuestion(
            f'Which candidate is the correct value of the field "{field.id}" ({field.description}) in the document '
            "in the state? Pick the exact substring the document presents as this field's value.",
            criteria,
        )
        state_fields.append({"id": field.key, "description": field.description, "pattern": field.pattern})

    # No field with candidates: nothing is asked, and the frame reports no provider (`frame`).
    evaluation = (
        await runtime.ask({"purpose": args.get("purpose"), "document": document, "fields": state_fields}, questions)
        if state_fields
        else None
    )
    answers = evaluation.answers if evaluation is not None else {}

    results = [_result(field, answers.get(field.key), auto_accept, minimum_margin) for field in fields]
    if any(field.truncated or field.too_long > 0 for field in fields):
        ledger.note("context")  # an incomplete candidate universe: Policy never auto-accepts over it
    item_actions = caller_actions(r["status"] for r in results)
    return ToolResult(
        frame(
            "jev_extract",
            evaluation,
            {
                "summary": {
                    "fields": len(results),
                    "extracted": sum(1 for r in results if r["value"] is not None),
                    "auto": sum(1 for r in results if r["status"] == "auto"),
                    "review": sum(1 for r in results if r["status"] == "review"),
                    "not_found": sum(1 for r in results if r["status"] == "not_found"),
                    "invalid": sum(1 for r in results if r["status"] in ("invalid_pattern", "invalid_response")),
                },
                "thresholds": {"auto_accept": auto_accept, "minimum_margin": minimum_margin},
                "results": results,
            },
            model=runtime.model,
        ),
        action=headline(item_actions),
        item_actions=item_actions,
        truncated=ledger.scopes,
    )


TOOL = JevTool(DEFINITION, handle)
