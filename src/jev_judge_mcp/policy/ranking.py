"""Ranking policy for jev_find and jev_rerank (`lib.ts:90-104`, `lib.ts:210-214`)."""

from collections.abc import Mapping, Sequence
from typing import Literal

from jev_judge_mcp.policy.thresholds import EXISTS_ABSENT_BELOW, EXISTS_FOUND_AT

type ExistsVerdict = Literal["answered", "partial", "absent"]


def exists_verdict(exists: float, found: float = EXISTS_FOUND_AT, absent: float = EXISTS_ABSENT_BELOW) -> ExistsVerdict:
    """`existsVerdict`: answered at or above `found`, absent below `absent`, else partial."""
    if exists >= found:
        return "answered"
    return "absent" if exists < absent else "partial"


def rank_candidates(
    candidates: Sequence[Mapping[str, object]], probabilities: Mapping[str, float]
) -> list[dict[str, object]]:
    """`rankCandidates`: each candidate plus its `probability`, descending; ties keep caller order.

    A candidate id missing from `probabilities` ranks at 0. `sorted` is stable, as the reference's
    index tie-break is.
    """
    scored = [(probabilities.get(str(candidate["id"]), 0.0), candidate) for candidate in candidates]
    return [{**candidate, "probability": p} for p, candidate in _descending(scored)]


def rerank_by_score(candidates: Sequence[Mapping[str, object]], scores: Sequence[float]) -> list[dict[str, object]]:
    """`rerankByScore`: each candidate plus its index-aligned `relevance`, descending; ties keep caller order.

    Scores are validated before this runs. The 0 for a missing score only guards a wiring mistake
    (fewer scores than candidates), never a model answer.
    """
    scored = [(scores[i] if i < len(scores) else 0.0, candidate) for i, candidate in enumerate(candidates)]
    return [{**candidate, "relevance": r} for r, candidate in _descending(scored)]


def _descending[T](scored: list[tuple[float, T]]) -> list[tuple[float, T]]:
    return sorted(scored, key=lambda pair: pair[0], reverse=True)
