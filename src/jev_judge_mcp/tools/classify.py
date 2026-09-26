"""jev_classify: batch-assign items to classes from a shared catalog (`index.ts:398-526`)."""

from typing import Any

from jev_judge_mcp.domain import ChoiceQuestion, Question
from jev_judge_mcp.limits import CLASSIFY
from jev_judge_mcp.policy import DEFAULT_CLASSIFY_AUTO_ACCEPT, DEFAULT_MINIMUM_MARGIN
from jev_judge_mcp.tools.base import JevTool, Runtime, ToolError, ToolResult, caller_actions, define, frame, headline
from jev_judge_mcp.tools.observed import classification_decision, fail_closed, validate_choice
from jev_judge_mcp.validation import margin, top_probability
from jev_judge_mcp.validation.caps import CapLedger, classify_budget_error, exceeds

DEFAULT_PURPOSE = "Assign each item to exactly one class."

DEFINITION = define(
    "jev_classify",
    "Classify items against a shared label set",
    "Assign each item to one class from a shared catalog with TypeSafe Jev, in one batched request: the class "
    "catalog is sent once and every item becomes an independent Choice question. Returns per item: the chosen "
    "class, the full distribution, confidence, winner-to-runner-up margin, and an auto-versus-review decision. Auto "
    "requires both a high top probability (default 0.85) and a clear margin (default 0.50); everything else is "
    "flagged for review. Include a manual_review class in the catalog if you want an explicit escape hatch; the tool "
    "never invents one.",
    {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"id": {"type": "string"}, "text": {"type": "string"}},
                    "required": ["text"],
                    "additionalProperties": False,
                },
                "minItems": CLASSIFY.items_min,
                "maxItems": CLASSIFY.items_max,
                "description": f"Items to classify: each one an object {{id, text}} with text required. Text is "
                f"truncated at {CLASSIFY.item_units} characters; send bounded excerpts, not whole documents.",
            },
            "classes": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"id": {"type": "string"}, "description": {"type": "string"}},
                    "required": ["description"],
                    "additionalProperties": False,
                },
                "minItems": CLASSIFY.classes_min,
                "maxItems": CLASSIFY.classes_max,
                "description": "Shared class catalog: each class an object {id, description} with description "
                "required. Strong descriptions carry the decision: a precise definition, "
                "what belongs, what does not, precedence over overlapping classes, and a short example.",
            },
            "purpose": {"type": "string", "description": "What this classification is for; shared across all items."},
            "context": {
                "anyOf": [{"type": "string"}, {"type": "object", "additionalProperties": {}}],
                "description": "Shared context available to every item's judgment: policies, catalogs, anything "
                "stable.",
            },
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
        "required": ["items", "classes"],
        "additionalProperties": False,
    },
)


def _opaque(
    raw: list[dict[str, str]], kind: str, key_prefix: str, text_key: str, cap: int, ledger: CapLedger
) -> list[tuple[str, str, str]]:
    """`(external id, wire key, capped text)` per entry.

    A caller id is kept verbatim. An omitted id becomes `{kind}{index}`. Either form that repeats
    an id already used in this list raises `Duplicate {kind} id` before the caller asks the
    provider (ADR-0031). The reference does not check a generated id against a supplied one.
    """
    seen: set[str] = set()
    out: list[tuple[str, str, str]] = []
    for index, entry in enumerate(raw):
        supplied = entry.get("id")
        external = supplied if supplied is not None else f"{kind}{index}"
        if external in seen:
            raise ToolError(f"Duplicate {kind} id: {external}")
        seen.add(external)
        out.append((external, f"{key_prefix}{index}", ledger.text(entry[text_key], cap, "item")))
    return out


async def handle(args: dict[str, Any], runtime: Runtime) -> ToolResult:
    auto_accept: float = args.get("auto_accept", DEFAULT_CLASSIFY_AUTO_ACCEPT)
    minimum_margin: float = args.get("minimum_margin", DEFAULT_MINIMUM_MARGIN)

    # Opaque wire keys (i0, c0) so sanitizing can never rename or collide caller ids.
    ledger = CapLedger()
    items = _opaque(args["items"], "item", "i", "text", CLASSIFY.item_units, ledger)
    classes = _opaque(args["classes"], "class", "c", "description", CLASSIFY.class_description_units, ledger)
    if exceeds(len(items) * len(classes), CLASSIFY.item_class_pairs):
        raise ToolError(classify_budget_error(len(items), len(classes), CLASSIFY.item_class_pairs))

    # The catalog is sent once in state; each question carries only its own item.
    state = {
        "purpose": args.get("purpose", DEFAULT_PURPOSE),
        "context": args.get("context"),
        "classes": [{"id": key, "description": description} for _, key, description in classes],
    }
    class_keys = [key for _, key, _ in classes]
    criteria = dict.fromkeys(class_keys)
    questions: dict[str, Question] = {
        key: ChoiceQuestion(
            {"task": "Which class does this item belong to?", "item": {"id": key, "text": text}}, criteria
        )
        for _, key, text in items
    }
    evaluation = await runtime.ask(state, questions)

    key_to_external = {key: external for external, key, _ in classes}
    results: list[dict[str, object]] = []
    for external, key, _ in items:
        answer = validate_choice(evaluation.answers.get(key), class_keys)
        if answer is None:
            closed = fail_closed("classify")
            assert closed != "status"
            results.append(
                {
                    "id": external,
                    "status": "invalid_response",
                    "classification": None,
                    "probabilities": None,
                    "confidence": None,
                    "margin": None,
                    "decision": closed,
                }
            )
            continue
        gap = margin(answer.probabilities)
        top = top_probability(answer)
        results.append(
            {
                "id": external,
                "classification": key_to_external.get(answer.choice, answer.choice),
                "probabilities": {
                    class_external: answer.probabilities.get(class_key, 0) for class_external, class_key, _ in classes
                },
                "confidence": answer.confidence,
                "margin": gap,
                "top_probability": top,
                "decision": classification_decision(top, gap, auto_accept, minimum_margin),
            }
        )

    by_class: dict[str, int] = {}
    for result in results:
        classification = result["classification"]
        if isinstance(classification, str):
            by_class[classification] = by_class.get(classification, 0) + 1

    item_actions = caller_actions(r["decision"] for r in results)
    return ToolResult(
        frame(
            "jev_classify",
            evaluation,
            {
                "summary": {
                    "items": len(results),
                    "auto": sum(1 for r in results if r["decision"] == "auto"),
                    "review": sum(
                        1 for r in results if r["decision"] == "review" and r.get("status") != "invalid_response"
                    ),
                    "invalid_response": sum(1 for r in results if r.get("status") == "invalid_response"),
                    "by_class": by_class,
                },
                "thresholds": {"auto_accept": auto_accept, "minimum_margin": minimum_margin},
                "results": results,
            },
        ),
        action=headline(item_actions),
        item_actions=item_actions,
        truncated=ledger.scopes,
    )


TOOL = JevTool(DEFINITION, handle)
