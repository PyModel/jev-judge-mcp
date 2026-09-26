"""jev_rerank: score every candidate's relevance and return them sorted (`index.ts:676-785`)."""

from typing import Any, cast

from jev_judge_mcp.domain import NoulCriteria, NoulQuestion, Question
from jev_judge_mcp.limits import CANDIDATES, RERANK
from jev_judge_mcp.serialize import to_fixed
from jev_judge_mcp.text import length
from jev_judge_mcp.tools.base import JevTool, Runtime, ToolError, ToolResult, define, frame
from jev_judge_mcp.tools.common import candidates_schema
from jev_judge_mcp.tools.observed import fail_closed, rerank_by_score, validate_noul
from jev_judge_mcp.validation.caps import CapLedger, candidate_budget_error, exceeds

RELEVANCE_CRITERIA = NoulCriteria(
    "The candidate addresses the subject the query asks about, or provides what it seeks",
    "The candidate is about a different subject, or only shares vocabulary with the query",
)

DEFINITION = define(
    "jev_rerank",
    "Score every candidate's relevance and return them sorted",
    "Rerank candidates against a query with TypeSafe Jev: one independent relevance probability per candidate, all "
    "in a single request, then sorted by score. Unlike jev_find (which picks one best answer), rerank scores every "
    "candidate so the full ordering survives. TypeSafe's rerank cookbook reports that on the CLERC benchmark this "
    "pattern lifted top-1 from 5% to 18% and top-10 from 38% to 62% (docs.typesafe.ai/cookbooks). Use for "
    f"retrieval ordering, dedup triage, or feed ranking across up to {CANDIDATES.max_items} candidates.",
    {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "minLength": RERANK.query_min,
                "maxLength": RERANK.query_max,
                "description": "What relevance is measured against, in natural language.",
            },
            "candidates": candidates_schema(
                f"Candidates to search: each one an object {{id, text}} with text required. "
                f"Up to {CANDIDATES.max_items} in one call; texts are truncated at "
                f"{CANDIDATES.text_units} chars."
            ),
            "top_k": {
                "type": "integer",
                "minimum": RERANK.top_k_min,
                "maximum": RERANK.top_k_max,
                "description": "How many ranked candidates to return. Default: all.",
            },
        },
        "required": ["query", "candidates"],
        "additionalProperties": False,
    },
)


def _external_ids(raw: list[dict[str, str]]) -> list[str]:
    """Caller ids verbatim and unique; an omitted id gets `candidate{i}`, suffixed past every used id."""
    supplied: set[str] = set()
    for candidate in raw:
        if "id" in candidate:
            if candidate["id"] in supplied:
                raise ToolError(f"Duplicate candidate id: {candidate['id']}")
            supplied.add(candidate["id"])
    used = set(supplied)
    ids: list[str] = []
    for index, candidate in enumerate(raw):
        if "id" in candidate:
            external = candidate["id"]
        else:
            external = f"candidate{index}"
            suffix = 2
            while external in used:
                external = f"candidate{index}_{suffix}"
                suffix += 1
        used.add(external)
        ids.append(external)
    return ids


async def handle(args: dict[str, Any], runtime: Runtime) -> ToolResult:
    query: str = args["query"]
    top_k: int | None = int(args["top_k"]) if "top_k" in args else None
    ids = _external_ids(args["candidates"])
    ledger = CapLedger()
    texts = [ledger.text(candidate["text"], CANDIDATES.text_units, "item") for candidate in args["candidates"]]
    total = sum(length(text) for text in texts)
    if exceeds(total, RERANK.aggregate_candidate_units):
        raise ToolError(candidate_budget_error(total, RERANK.aggregate_candidate_units, "Split the batch."))

    # The query is sent once in state; each question carries only its own candidate.
    questions: dict[str, Question] = {
        f"rel_{i}": NoulQuestion(
            f"Is candidate c{i} relevant to the query in the state? Candidate c{i}: {text}", RELEVANCE_CRITERIA
        )
        for i, text in enumerate(texts)
    }
    evaluation = await runtime.ask({"query": query}, questions)

    # One invalid answer makes the whole ordering untrustworthy: never sort it as a confident zero.
    scores = [validate_noul(evaluation.answers.get(f"rel_{i}")) for i in range(len(texts))]
    if any(score is None for score in scores):
        assert fail_closed("rerank") == "status"
        return ToolResult(
            frame(
                "jev_rerank",
                evaluation,
                {
                    "query": query,
                    "status": "invalid_response",
                    "ranked": None,
                },
            ),
            truncated=ledger.scopes,
        )

    ranked = rerank_by_score(
        [{"id": external, "text": text} for external, text in zip(ids, texts, strict=True)],
        [score for score in scores if score is not None],
    )
    returned = ranked[:top_k] if top_k else ranked
    return ToolResult(
        frame(
            "jev_rerank",
            evaluation,
            {
                "query": query,
                "summary": {"candidates": len(texts), "returned": len(returned)},
                "ranked": [
                    {
                        "rank": rank,
                        "id": candidate["id"],
                        "relevance": float(to_fixed(cast(float, candidate["relevance"]), 4)),
                        "text": candidate["text"],
                    }
                    for rank, candidate in enumerate(returned, start=1)
                ],
            },
        ),
        truncated=ledger.scopes,
    )


TOOL = JevTool(DEFINITION, handle)
