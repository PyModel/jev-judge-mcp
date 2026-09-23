"""jev_compare: the fact relation between two passages, optionally per aspect (`index.ts:790-879`)."""

from typing import Any

from jev_judge_mcp.domain import ChoiceQuestion, Question
from jev_judge_mcp.limits import COMPARE
from jev_judge_mcp.policy import DEFAULT_CLASSIFY_AUTO_ACCEPT, DEFAULT_MINIMUM_MARGIN
from jev_judge_mcp.tools.base import JevTool, Runtime, ToolResult, caller_actions, define, frame, headline
from jev_judge_mcp.tools.observed import classification_decision, fail_closed, validate_choice
from jev_judge_mcp.validation import margin, top_probability
from jev_judge_mcp.validation.caps import CapLedger

COMPARE_RELATIONS = {
    "same_fact": "Both passages state the same underlying fact or claim",
    "contradicts": "The passages state opposing facts about the same subject",
    "different_facts": "The passages discuss different subjects or make non-overlapping claims",
}
"""`COMPARE_RELATIONS` (`lib.ts:188-192`)."""

ASPECT_RELATIONS = {
    "same_fact": "Both passages make comparable assertions about this aspect and they agree",
    "contradicts": "Both passages address this aspect and their assertions conflict",
    "different_facts": "The passages do not both make a comparable assertion about this aspect: at least one does "
    "not address it, or their mentions do not overlap",
}
"""`ASPECT_RELATIONS` (`lib.ts:199-204`): per aspect, the third outcome usually means one passage is silent."""

DEFINITION = define(
    "jev_compare",
    "Compare two passages for factual agreement",
    "Judge the relation between two passages with TypeSafe Jev: same_fact, contradicts, or different_facts, with "
    "the full probability distribution, confidence, and an auto-versus-review decision. Optionally supply aspects "
    "(price, date, method, …) and each gets an independent per-aspect judgment in the same single request. Use for "
    "source reconciliation, changelog-vs-code drift, or merge sanity checks. The request supplies no evidence beyond "
    "the two passages, so a same_fact verdict means they agree with each other, not that they are true.",
    {
        "type": "object",
        "properties": {
            "passage_a": {
                "type": "string",
                "minLength": COMPARE.passage_min,
                "maxLength": COMPARE.passage_max,
                "description": f"First passage. Rejected above {COMPARE.passage_max:,} characters.",
            },
            "passage_b": {
                "type": "string",
                "minLength": COMPARE.passage_min,
                "maxLength": COMPARE.passage_max,
                "description": f"Second passage. Rejected above {COMPARE.passage_max:,} characters.",
            },
            "aspects": {
                "type": "array",
                "items": {"type": "string", "minLength": COMPARE.aspect_min, "maxLength": COMPARE.aspect_max},
                "maxItems": COMPARE.aspects_max,
                "description": "Named aspects to judge independently (e.g. 'price', 'launch date'). Each tests one "
                "property.",
            },
            "purpose": {"type": "string", "description": "What this comparison is for; helps disambiguate overlap."},
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
        "required": ["passage_a", "passage_b"],
        "additionalProperties": False,
    },
)


async def handle(args: dict[str, Any], runtime: Runtime) -> ToolResult:
    auto_accept: float = args.get("auto_accept", DEFAULT_CLASSIFY_AUTO_ACCEPT)
    minimum_margin: float = args.get("minimum_margin", DEFAULT_MINIMUM_MARGIN)
    aspects: list[str] = args.get("aspects", [])

    questions: dict[str, Question] = {
        "overall": ChoiceQuestion(
            "Do the two passages state the same underlying fact, contradict each other, or discuss different facts?",
            COMPARE_RELATIONS,
        )
    }
    for i, aspect in enumerate(aspects):
        questions[f"aspect_{i}"] = ChoiceQuestion(
            f'Judging only the aspect "{aspect}" of the two passages in the state, which relation holds?',
            ASPECT_RELATIONS,
        )
    # The schema rejects a passage over its cap first, so this ledger never records a cut.
    ledger = CapLedger()
    state = {
        "purpose": args.get("purpose"),
        "passage_a": ledger.text(args["passage_a"], COMPARE.passage_max, "context"),
        "passage_b": ledger.text(args["passage_b"], COMPARE.passage_max, "context"),
        "aspects": aspects,
    }
    evaluation = await runtime.ask(state, questions)

    def judge(raw: object) -> dict[str, object]:
        answer = validate_choice(raw, COMPARE_RELATIONS)
        if answer is None:
            closed = fail_closed("compare")
            assert closed != "status"
            return {
                "relation": None,
                "probabilities": None,
                "confidence": None,
                "margin": None,
                "decision": closed,
                "status": "invalid_response",
            }
        gap = margin(answer.probabilities)
        return {
            "relation": answer.choice,
            "probabilities": answer.probabilities,
            "confidence": answer.confidence,
            "margin": gap,
            "decision": classification_decision(top_probability(answer), gap, auto_accept, minimum_margin),
        }

    overall = judge(evaluation.answers.get("overall"))
    return ToolResult(
        frame(
            "jev_compare",
            evaluation,
            {
                "overall": overall,
                "aspects": [
                    {"aspect": aspect, **judge(evaluation.answers.get(f"aspect_{i}"))}
                    for i, aspect in enumerate(aspects)
                ],
                "thresholds": {"auto_accept": auto_accept, "minimum_margin": minimum_margin},
            },
        ),
        action=headline(caller_actions([overall["decision"]])),  # the aspects are not the headline
        truncated=ledger.scopes,
    )


TOOL = JevTool(DEFINITION, handle)
