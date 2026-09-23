"""jev_find: semantic search over candidates (`index.ts:325-392`)."""

from typing import Any

from jev_judge_mcp.domain import ChoiceQuestion, NoulCriteria, NoulQuestion
from jev_judge_mcp.ids import ensure_unique_ids
from jev_judge_mcp.limits import CANDIDATES, FIND
from jev_judge_mcp.serialize import to_fixed
from jev_judge_mcp.tools.base import JevTool, Runtime, ToolResult, define, frame
from jev_judge_mcp.tools.common import candidates_schema
from jev_judge_mcp.tools.observed import exists_verdict, fail_closed, rank_candidates, validate_choice, validate_noul
from jev_judge_mcp.validation.caps import CapLedger

EXISTS_CRITERIA = NoulCriteria(
    "At least one candidate states or directly implies the answer", "No candidate addresses this"
)

DEFINITION = define(
    "jev_find",
    "Semantic search over candidates",
    "Rank candidates against a plain-language query with TypeSafe Jev — no embeddings needed. One Choice scores "
    "every candidate id by how well it answers the query, plus a Noul checks whether any candidate addresses the "
    "query at all (so a confident 'top hit' cannot masquerade as an answer). Pattern: "
    f"docs.typesafe.ai/cookbooks/semantic_find. Use for 'which file/note/line covers X' across up to "
    f"{CANDIDATES.max_items} candidates.",
    {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "minLength": FIND.query_min,
                "description": "What you are looking for, in natural language.",
            },
            "candidates": candidates_schema(
                f"Candidates to search. Up to {CANDIDATES.max_items} in one call; texts are truncated at "
                f"{CANDIDATES.text_units} chars."
            ),
            "top_k": {
                "type": "integer",
                "minimum": FIND.top_k_min,
                "maximum": FIND.top_k_max,
                "description": f"How many ranked candidates to return. Default {FIND.top_k_default}.",
            },
        },
        "required": ["query", "candidates"],
        "additionalProperties": False,
    },
)


async def handle(args: dict[str, Any], runtime: Runtime) -> ToolResult:
    query: str = args["query"]
    top_k = int(args.get("top_k", FIND.top_k_default))
    ledger = CapLedger()
    candidates = ensure_unique_ids(
        [
            {"id": c.get("id", ""), "text": ledger.text(c["text"], CANDIDATES.text_units, "item")}
            for c in args["candidates"]
        ],
        "candidate",
    ).items
    ids = [str(c["id"]) for c in candidates]

    questions = {
        "best": ChoiceQuestion(f'Which candidate contains the best answer to: "{query}"?', dict.fromkeys(ids)),
        "exists": NoulQuestion(f'Does any candidate address or answer: "{query}"?', EXISTS_CRITERIA),
    }
    evaluation = await runtime.ask({"query": query, "candidates": candidates}, questions)
    exists = validate_noul(evaluation.answers.get("exists"))
    best = validate_choice(evaluation.answers.get("best"), ids)

    if exists is None or best is None:
        # A missing answer is neither "no match" nor "no ranking": report the protocol failure.
        assert fail_closed("find") == "status"
        return ToolResult(
            frame(
                "jev_find",
                evaluation,
                {
                    "query": query,
                    "status": "invalid_response",
                    "exists": exists,
                    "exists_verdict": None,
                    "top": [],
                    "reason": "missing or malformed best or exists answer; cannot rank safely",
                },
            ),
            truncated=ledger.scopes,
        )

    ranked = rank_candidates(candidates, best.probabilities)[:top_k]
    return ToolResult(
        frame(
            "jev_find",
            evaluation,
            {
                "query": query,
                "exists": exists,
                "exists_verdict": exists_verdict(exists),
                "top": [
                    {
                        "id": c["id"],
                        "probability": float(to_fixed(best.probabilities.get(str(c["id"]), 0.0), 4)),
                        "text": c["text"],
                    }
                    for c in ranked
                ],
            },
        ),
        truncated=ledger.scopes,
    )


TOOL = JevTool(DEFINITION, handle)
