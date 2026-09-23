"""Ranking baselines for jev_rerank and jev_find cases, over the same candidate ids the tools assign."""

import math
import re
from collections import Counter
from collections.abc import Mapping, Sequence

from evals.scorers.fields import as_object, is_object
from jev_judge_mcp.ids import ensure_unique_ids

BM25_K1 = 1.5
BM25_B = 0.75
_TOKEN = re.compile(r"\w+")


def _candidates(raw: Sequence[object]) -> list[dict[str, object]]:
    """Tool-shaped candidates: a string is `{text}`, and ids are made unique exactly as the tools do."""
    items = [{"text": item} if isinstance(item, str) else dict(_as_mapping(item)) for item in raw]
    return ensure_unique_ids(items, "candidate").items


def _as_mapping(item: object) -> Mapping[str, object]:
    if not is_object(item):
        raise TypeError(f"a candidate must be a string or an object, got {type(item).__name__}")
    return as_object(item)


def _tokens(text: str) -> list[str]:
    return _TOKEN.findall(text.lower())


def original_order(candidates: Sequence[object]) -> list[str]:
    return [str(item["id"]) for item in _candidates(candidates)]


def bm25(query: str, candidates: Sequence[object]) -> list[str]:
    """Okapi BM25 over the candidate set as the corpus; ties keep the original order."""
    items = _candidates(candidates)
    documents = [_tokens(str(item.get("text", ""))) for item in items]
    if not documents:
        return []
    average_length = math.fsum(len(d) for d in documents) / len(documents) or 1.0
    document_frequency = Counter(term for d in documents for term in set(d))
    n = len(documents)

    def score(document: list[str]) -> float:
        counts = Counter(document)
        total = 0.0
        for term in set(_tokens(query)):
            if term not in counts:
                continue
            idf = math.log(1 + (n - document_frequency[term] + 0.5) / (document_frequency[term] + 0.5))
            tf = counts[term]
            total += idf * tf * (BM25_K1 + 1) / (tf + BM25_K1 * (1 - BM25_B + BM25_B * len(document) / average_length))
        return total

    scores = [score(d) for d in documents]
    order = sorted(range(n), key=lambda i: -scores[i])
    return [str(items[i]["id"]) for i in order]


def embeddings(query: str, candidates: Sequence[object]) -> list[str]:
    """The embeddings baseline needs an embedding model, which no offline run has."""
    raise NotImplementedError("the embeddings baseline needs a pinned embedding model; it runs only with live L3")
