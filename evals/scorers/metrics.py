"""Generic metric math. Pure functions; an undefined metric returns `None`, never a made-up 0 or 1."""

import math
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass


def ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def mean(values: Sequence[float | None]) -> float | None:
    """Mean of the defined values; `None` when none is defined."""
    defined = [v for v in values if v is not None]
    return math.fsum(defined) / len(defined) if defined else None


def recall(gold: Sequence[bool], predicted: Sequence[bool]) -> float | None:
    """TP / gold positives."""
    return ratio(sum(g and p for g, p in zip(gold, predicted, strict=True)), sum(gold))


def precision(gold: Sequence[bool], predicted: Sequence[bool]) -> float | None:
    """TP / predicted positives."""
    return ratio(sum(g and p for g, p in zip(gold, predicted, strict=True)), sum(predicted))


def f1(gold: Sequence[bool], predicted: Sequence[bool]) -> float | None:
    """`None` when the class never occurs in gold or prediction; 0 when it occurs but is never hit."""
    tp = sum(g and p for g, p in zip(gold, predicted, strict=True))
    denominator = sum(gold) + sum(predicted)
    return 2 * tp / denominator if denominator else None


def macro_f1(gold: Sequence[str], predicted: Sequence[str | None], labels: Collection[str]) -> float | None:
    """Unweighted mean of per-label F1 over `labels`; a `None` prediction (invalid) is wrong for every label."""
    return mean([f1([g == label for g in gold], [p == label for p in predicted]) for label in labels])


def micro_f1(gold: Sequence[str], predicted: Sequence[str | None], labels: Collection[str]) -> float | None:
    """F1 over pooled TP/FP/FN across `labels`."""
    tp = sum(g == p and g in labels for g, p in zip(gold, predicted, strict=True))
    fp = sum(p is not None and p in labels and p != g for g, p in zip(gold, predicted, strict=True))
    fn = sum(g in labels and g != p for g, p in zip(gold, predicted, strict=True))
    return ratio(2 * tp, 2 * tp + fp + fn)


def accuracy(gold: Sequence[object], predicted: Sequence[object]) -> float | None:
    return ratio(sum(g == p for g, p in zip(gold, predicted, strict=True)), len(gold))


def brier(probabilities: Sequence[Mapping[str, float]], gold: Sequence[str], labels: Collection[str]) -> float | None:
    """Multiclass Brier score: mean over rows of the summed squared error across `labels`."""
    rows = [
        math.fsum((row.get(label, 0.0) - (1.0 if label == g else 0.0)) ** 2 for label in labels)
        for row, g in zip(probabilities, gold, strict=True)
    ]
    return mean(rows)


def expected_calibration_error(confidences: Sequence[float], correct: Sequence[bool], bins: int = 10) -> float | None:
    """Equal-width ECE. Bin i holds (i/bins, (i+1)/bins]; a confidence of exactly 0 falls in bin 0."""
    if not confidences:
        return None
    buckets: list[list[tuple[float, bool]]] = [[] for _ in range(bins)]
    for confidence, hit in zip(confidences, correct, strict=True):
        index = max(0, min(bins - 1, math.ceil(round(confidence * bins, 9)) - 1))  # 0.3 * 10 is 3.0000000000000004
        buckets[index].append((confidence, hit))
    total = len(confidences)
    return math.fsum(
        len(bucket)
        / total
        * abs(math.fsum(c for c, _ in bucket) / len(bucket) - sum(h for _, h in bucket) / len(bucket))
        for bucket in buckets
        if bucket
    )


def auroc(scores: Sequence[float], positive: Sequence[bool]) -> float | None:
    """Mann-Whitney AUROC with mid-ranks for ties; `None` unless both classes are present."""
    n_pos = sum(positive)
    n_neg = len(positive) - n_pos
    if n_pos == 0 or n_neg == 0:
        return None
    order = sorted(range(len(scores)), key=lambda i: scores[i])
    ranks = [0.0] * len(scores)
    start = 0
    while start < len(order):
        end = start
        while end + 1 < len(order) and scores[order[end + 1]] == scores[order[start]]:
            end += 1
        for i in order[start : end + 1]:
            ranks[i] = (start + end) / 2 + 1
        start = end + 1
    positive_ranks = math.fsum(r for r, p in zip(ranks, positive, strict=True) if p)
    return (positive_ranks - n_pos * (n_pos + 1) / 2) / (n_pos * n_neg)


def average_precision(scores: Sequence[float], positive: Sequence[bool]) -> float | None:
    """PR-AUC as step-wise average precision; tied scores enter as one threshold. `None` with no positives."""
    n_pos = sum(positive)
    if n_pos == 0:
        return None
    total, previous_recall = 0.0, 0.0
    for threshold in sorted(set(scores), reverse=True):
        flagged = [p for s, p in zip(scores, positive, strict=True) if s >= threshold]
        current_recall = sum(flagged) / n_pos
        total += (current_recall - previous_recall) * sum(flagged) / len(flagged)
        previous_recall = current_recall
    return total


@dataclass(frozen=True, slots=True)
class FixedRatePoint:
    threshold: float
    """Flag when score >= threshold; `inf` when even the top score exceeds the false-positive budget."""
    recall: float | None
    false_positive_rate: float | None


def recall_at_false_positive_rate(
    scores: Sequence[float], positive: Sequence[bool], max_false_positive_rate: float
) -> FixedRatePoint:
    """Highest recall among thresholds whose false-positive rate stays within the budget."""
    n_pos = sum(positive)
    n_neg = len(positive) - n_pos
    best = FixedRatePoint(math.inf, ratio(0, n_pos), ratio(0, n_neg))
    for threshold in sorted(set(scores), reverse=True):
        flagged = [p for s, p in zip(scores, positive, strict=True) if s >= threshold]
        fpr = ratio(flagged.count(False), n_neg)
        if fpr is not None and fpr > max_false_positive_rate:
            break  # lower thresholds only flag more negatives
        best = FixedRatePoint(threshold, ratio(sum(flagged), n_pos), fpr)
    return best


def reciprocal_rank(ranked: Sequence[str], relevant: Collection[str]) -> float:
    """1 / rank of the first relevant id; 0 when none is ranked."""
    return next((1 / rank for rank, item in enumerate(ranked, 1) if item in relevant), 0.0)


def ndcg_at_k(ranked: Sequence[str], grades: Mapping[str, float], k: int = 10) -> float | None:
    """NDCG@k with gain 2^grade - 1 and log2 discount; `None` when no item has a positive grade."""

    def dcg(values: Sequence[float]) -> float:
        return math.fsum((2**g - 1) / math.log2(rank + 1) for rank, g in enumerate(values[:k], 1))

    ideal = dcg(sorted(grades.values(), reverse=True))
    return dcg([grades.get(item, 0.0) for item in ranked]) / ideal if ideal > 0 else None


def kendall_tau_b(ranked: Sequence[str], grades: Mapping[str, float]) -> float | None:
    """Kendall tau-b between the predicted order and the gold grades; an ungraded id has grade 0, as in NDCG.

    `None` when either side has no variation (fewer than two items, or all grades equal).
    """
    items = list(ranked)
    concordant = discordant = ties_grade = 0
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            # items[i] is ranked above items[j], so a higher gold grade for i is concordant.
            difference = grades.get(items[i], 0.0) - grades.get(items[j], 0.0)
            if difference > 0:
                concordant += 1
            elif difference < 0:
                discordant += 1
            else:
                ties_grade += 1
    pairs = len(items) * (len(items) - 1) // 2
    denominator = math.sqrt(pairs * (pairs - ties_grade))  # a ranking has no ties of its own
    return (concordant - discordant) / denominator if denominator else None
