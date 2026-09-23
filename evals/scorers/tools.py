"""Per-tool L3 scorers: the ROADMAP P7 metric table, one function per tool.

Each scorer reads the tool's own JSON result (the `text` of a successful `tools/call`) next to a gold
label, and reports its primary metric plus the "also" metrics. Actions and decisions are read from the
result, never recomputed: the eval grades the server's policy, it does not carry a second copy of it.
A result that is an error or lacks a judgment scores as wrong for accuracy-type metrics and is left
out of score-based ones (AUROC, Brier, ECE); `invalid` counts those rows so a report can show them.
Gold shapes are documented per scorer and in evals/README.md.

`judgments` is the one per-row join of result and gold (ADR-0028): calibration rows and the per-item
scorers (verify, classify, extract) read it instead of re-reading the result.
"""

from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from typing import cast

from evals.baselines.ranking import bm25, original_order
from evals.calibration.bounds import clopper_pearson_upper
from evals.scorers.fields import Json, as_number, as_object, as_objects, by_id, is_object
from evals.scorers.metrics import (
    accuracy,
    auroc,
    average_precision,
    brier,
    expected_calibration_error,
    kendall_tau_b,
    macro_f1,
    mean,
    micro_f1,
    ndcg_at_k,
    precision,
    ratio,
    recall,
    recall_at_false_positive_rate,
    reciprocal_rank,
)

Metric = float | int | None


@dataclass(frozen=True, slots=True)
class Example:
    """One scored case: the tool arguments, the gold label, and the parsed tool result."""

    id: str
    family: str
    input: Json
    gold: Json
    output: Json


@dataclass(frozen=True, slots=True)
class ToolScore:
    tool: str
    primary: str
    n: int
    metrics: dict[str, Metric] = field(default_factory=dict[str, Metric])

    @property
    def primary_value(self) -> Metric:
        return self.metrics[self.primary]


@dataclass(frozen=True, slots=True)
class Judgment:
    """One judged row: `key` is `case` or `case/item`; `predicted` is None when the result made no decision.

    `score` is the scalar the tool compares with its auto_accept threshold (None when the tool has no
    single-score AUTO decision); `auto` is whether the row's own action or decision is AUTO. A set
    `gold` (find, rerank, decide) holds every acceptable answer. Review's scored claim is "safe to
    apply": its `predicted` is "safe" whenever `safe_to_apply` is a number, so a row is correct exactly
    when the change is not defective.
    """

    key: str
    gold: object
    predicted: object
    score: float | None
    auto: bool

    @property
    def correct(self) -> bool:
        gold: object = self.gold
        if isinstance(gold, frozenset):
            return self.predicted in cast(frozenset[object], gold)
        return self.gold == self.predicted


def _relevant(example: Example) -> frozenset[object]:
    return frozenset(k for k, g in example.gold["relevance"].items() if g > 0)


# --- jev_verify -------------------------------------------------------------------------------------

VERIFY_VERDICTS = ("verified", "contradicted", "unsupported")
_RELATION_TO_VERDICT = {"supports": "verified", "contradicts": "contradicted", "says_nothing": "unsupported"}


def _judge_verify(example: Example) -> Iterator[Judgment]:
    results = by_id(example.output, "results")
    for claim_id, label in example.gold["claims"].items():
        result = results.get(claim_id, {})
        verdict = result.get("verdict")
        yield Judgment(
            f"{example.id}/{claim_id}",
            label,
            verdict if verdict in VERIFY_VERDICTS else None,
            as_number(result.get("confidence")),
            result.get("action") == "auto",
        )


def _labels(found: Sequence[Judgment]) -> tuple[list[str], list[str | None], list[bool]]:
    """Gold, predicted, and AUTO columns of string-labelled judgments."""
    gold = [str(j.gold) for j in found]
    predicted = [j.predicted if isinstance(j.predicted, str) else None for j in found]
    return gold, predicted, [j.auto for j in found]


def score_verify(examples: Sequence[Example], params: Json) -> ToolScore:
    """Gold `{"claims": {claim_id: "verified" | "contradicted" | "unsupported"}}`.

    ECE uses the result's `confidence`, the value the policy compares to auto_accept (not the top probability).
    """
    gold, predicted, auto = _labels([j for e in examples for j in _judge_verify(e)])
    probabilities: list[dict[str, float]] = []
    probability_gold: list[str] = []
    confidences: list[float] = []
    hits: list[bool] = []
    invalid = 0
    for example in examples:
        results = by_id(example.output, "results")
        for claim_id, label in example.gold["claims"].items():
            result = results.get(claim_id, {})
            verdict = result.get("verdict")
            raw = result.get("probabilities")
            if not is_object(raw):
                invalid += 1
                continue
            mapped = {
                verdict: as_number(as_object(raw).get(relation)) or 0.0
                for relation, verdict in _RELATION_TO_VERDICT.items()
            }
            probabilities.append(mapped)
            probability_gold.append(label)
            confidences.append(as_number(result.get("confidence")) or 0.0)
            hits.append(verdict == label)
    correct = [g == p for g, p in zip(gold, predicted, strict=True)]
    return ToolScore(
        "jev_verify",
        "contradiction_recall",
        len(gold),
        {
            "contradiction_recall": recall(
                [g == "contradicted" for g in gold], [p == "contradicted" for p in predicted]
            ),
            "macro_f1": macro_f1(gold, predicted, VERIFY_VERDICTS),
            "brier": brier(probabilities, probability_gold, VERIFY_VERDICTS),
            "ece": expected_calibration_error(confidences, hits),
            "selective_accuracy": ratio(sum(c and a for c, a in zip(correct, auto, strict=True)), sum(auto)),
            "auto_coverage": ratio(sum(auto), len(auto)),
            "invalid": invalid,
        },
    )


# --- jev_screen -------------------------------------------------------------------------------------


def _judge_screen(example: Example) -> Iterator[Judgment]:
    action = as_object(example.output.get("recommendation")).get("action")
    predicted = {"block": "injection", "pass": "clean"}.get(action) if isinstance(action, str) else None
    yield Judgment(example.id, "injection" if example.gold["injection"] else "clean", predicted, None, action == "pass")


def score_screen(examples: Sequence[Example], params: Json) -> ToolScore:
    """Gold `{"injection": bool, "skip": bool}`. Params: `max_false_block_rate` (required, no default).

    The fixed-rate operating point sweeps the injection probability; it is reported, not applied.
    """
    if "max_false_block_rate" not in params:
        raise ValueError(
            "jev_screen needs params.max_false_block_rate: the fixed false-block rate is a manifest choice"
        )
    scores: list[float] = []
    injection: list[bool] = []
    skipped: list[bool] = []
    should_skip: list[bool] = []
    invalid = 0
    for example in examples:
        value = as_number(as_object(example.output.get("probabilities")).get("injection"))
        if value is None:
            invalid += 1
        else:
            scores.append(value)
            injection.append(bool(example.gold["injection"]))
        skipped.append(as_object(example.output.get("recommendation")).get("action") == "skip")
        should_skip.append(bool(example.gold.get("skip", False)))
    point = recall_at_false_positive_rate(scores, injection, float(params["max_false_block_rate"]))
    return ToolScore(
        "jev_screen",
        "injection_recall_at_false_block_rate",
        len(examples),
        {
            "injection_recall_at_false_block_rate": point.recall,
            "operating_threshold": point.threshold,
            "false_block_rate": point.false_positive_rate,
            "pr_auc": average_precision(scores, injection),
            "skip_precision": precision(should_skip, skipped),
            "invalid": invalid,
        },
    )


# --- jev_find ---------------------------------------------------------------------------------------


def _ranked_ids(output: Json, key: str) -> list[str]:
    return [str(item.get("id")) for item in as_objects(output.get(key))]


def _judge_find(example: Example) -> Iterator[Judgment]:
    """No AUTO decision: the score is the top candidate's probability, a proposal (calibration/rows.py)."""
    top = as_objects(example.output.get("top"))
    predicted = str(top[0].get("id")) if top else None
    score = as_number(top[0].get("probability")) if top else None
    yield Judgment(example.id, _relevant(example), predicted, score, False)


def score_find(examples: Sequence[Example], params: Json) -> ToolScore:
    """Gold `{"relevance": {candidate_id: grade}, "verdict"?: "answered" | "partial" | "absent"}`.

    A case is answerable when some grade is positive; ranking metrics cover answerable cases only.
    """
    at_one: list[float] = []
    reciprocal: list[float] = []
    ndcg: list[float | None] = []
    exists: list[float] = []
    answerable_flags: list[bool] = []
    verdict_gold: list[object] = []
    verdict_predicted: list[object] = []
    invalid = 0
    for example in examples:
        grades: Mapping[str, float] = example.gold["relevance"]
        relevant = {k for k, g in grades.items() if g > 0}
        ranked = _ranked_ids(example.output, "top")
        if relevant:
            at_one.append(1.0 if ranked[:1] and ranked[0] in relevant else 0.0)
            reciprocal.append(reciprocal_rank(ranked, relevant))
            ndcg.append(ndcg_at_k(ranked, grades))
        value = as_number(example.output.get("exists"))
        if value is None:
            invalid += 1
        else:
            exists.append(value)
            answerable_flags.append(bool(relevant))
        if "verdict" in example.gold:
            verdict_gold.append(example.gold["verdict"])
            verdict_predicted.append(example.output.get("exists_verdict"))
    return ToolScore(
        "jev_find",
        "recall_at_1",
        len(examples),
        {
            "recall_at_1": mean(at_one),
            "exists_auroc": auroc(exists, answerable_flags),
            "mrr": mean(reciprocal),
            "ndcg_at_10": mean(ndcg),
            "verdict_accuracy": accuracy(verdict_gold, verdict_predicted),
            "invalid": invalid,
        },
    )


# --- jev_rerank -------------------------------------------------------------------------------------


def _ranking_metrics(rankings: Sequence[tuple[list[str], Mapping[str, float]]]) -> dict[str, Metric]:
    return {
        "ndcg_at_10": mean([ndcg_at_k(r, g) for r, g in rankings]),
        "mrr": mean([reciprocal_rank(r, {k for k, v in g.items() if v > 0}) for r, g in rankings]),
        "kendall_tau": mean([kendall_tau_b(r, g) for r, g in rankings]),
    }


def _judge_rerank(example: Example) -> Iterator[Judgment]:
    ranked = _ranked_ids(example.output, "ranked")
    yield Judgment(example.id, _relevant(example), ranked[0] if ranked else None, None, False)


def score_rerank(examples: Sequence[Example], params: Json) -> ToolScore:
    """Gold `{"relevance": {candidate_id: grade}}`. Baselines: original order and BM25 over the same input."""
    jev = _ranking_metrics([(_ranked_ids(e.output, "ranked"), e.gold["relevance"]) for e in examples])
    original = _ranking_metrics([(original_order(e.input["candidates"]), e.gold["relevance"]) for e in examples])
    lexical = _ranking_metrics(
        [(bm25(str(e.input["query"]), e.input["candidates"]), e.gold["relevance"]) for e in examples]
    )
    return ToolScore(
        "jev_rerank",
        "ndcg_at_10",
        len(examples),
        {
            **jev,
            "ndcg_at_10_original_order": original["ndcg_at_10"],
            "ndcg_at_10_bm25": lexical["ndcg_at_10"],
            "mrr_bm25": lexical["mrr"],
            "kendall_tau_bm25": lexical["kendall_tau"],
        },
    )


# --- jev_classify -----------------------------------------------------------------------------------


def _judge_classify(example: Example) -> Iterator[Judgment]:
    results = by_id(example.output, "results")
    for item_id, label in example.gold["labels"].items():
        result = results.get(item_id, {})
        classification = result.get("classification")
        yield Judgment(
            f"{example.id}/{item_id}",
            label,
            classification if isinstance(classification, str) else None,
            as_number(result.get("top_probability")),
            result.get("decision") == "auto",
        )


def score_classify(examples: Sequence[Example], params: Json) -> ToolScore:
    """Gold `{"labels": {item_id: class}}`. Selective accuracy is accuracy among AUTO decisions."""
    gold, predicted, auto = _labels([j for e in examples for j in _judge_classify(e)])
    labels = sorted(set(gold) | {p for p in predicted if p is not None})
    correct = [g == p for g, p in zip(gold, predicted, strict=True)]
    return ToolScore(
        "jev_classify",
        "selective_accuracy_auto",
        len(gold),
        {
            "selective_accuracy_auto": ratio(sum(c and a for c, a in zip(correct, auto, strict=True)), sum(auto)),
            "auto_coverage": ratio(sum(auto), len(auto)),
            "macro_f1": macro_f1(gold, predicted, labels),
            "micro_f1": micro_f1(gold, predicted, labels),
        },
    )


# --- jev_decide -------------------------------------------------------------------------------------

REQUIREMENT_ANSWERS = ("supported", "contradicted", "unknown")


ESCAPE = "escape"
"""The decide judgment when the recommendation escapes (ask_user, investigate, none)."""


def _judge_decide(example: Example) -> Iterator[Judgment]:
    recommendation = as_object(example.output.get("recommendation"))
    escaped, selected = recommendation.get("escaped"), recommendation.get("selected")
    predicted = ESCAPE if escaped is True else selected if escaped is False and isinstance(selected, str) else None
    acceptable = frozenset[object](example.gold["acceptable"] or (ESCAPE,))
    yield Judgment(example.id, acceptable, predicted, None, False)


def score_decide(examples: Sequence[Example], params: Json) -> ToolScore:
    """Gold `{"acceptable": [candidate_id], "checks"?: {"<candidate>#<requirement index>": answer}}`.

    An empty `acceptable` means the right move is an escape hatch. Overdecision is committing to a
    candidate (`escaped` false) when it should have escaped; an invalid recommendation (`escaped` null)
    did not decide, so it is not an overdecision but is wrong for escape-hatch accuracy.
    """
    overdecided: list[bool] = []
    escape_correct: list[bool] = []
    check_gold: list[str] = []
    check_predicted: list[str | None] = []
    for example in examples:
        escaped = as_object(example.output.get("recommendation")).get("escaped")
        should_escape = not example.gold["acceptable"]
        if should_escape:
            overdecided.append(escaped is False)
        escape_correct.append(escaped is should_escape)
        checks = {
            f"{check.get('candidate')}#{check.get('requirement')}": check.get("answer")
            for check in as_objects(example.output.get("checks"))
        }
        for key, answer in example.gold.get("checks", {}).items():
            check_gold.append(answer)
            got = checks.get(key)
            check_predicted.append(got if isinstance(got, str) else None)
    return ToolScore(
        "jev_decide",
        "overdecision_rate",
        len(examples),
        {
            "overdecision_rate": ratio(sum(overdecided), len(overdecided)),
            "escape_hatch_accuracy": ratio(sum(escape_correct), len(escape_correct)),
            "requirement_check_f1": macro_f1(check_gold, check_predicted, REQUIREMENT_ANSWERS),
        },
    )


# --- jev_compare ------------------------------------------------------------------------------------

COMPARE_RELATIONS = ("same_fact", "contradicts", "different_facts")
PERTURBATIONS = ("numeric", "negation")


def _judge_compare(example: Example) -> Iterator[Judgment]:
    overall = as_object(example.output.get("overall"))
    relation = overall.get("relation")
    if not isinstance(relation, str):
        relation = None
    score = as_number(as_object(overall.get("probabilities")).get(relation)) if relation is not None else None
    yield Judgment(example.id, example.gold["relation"], relation, score, overall.get("decision") == "auto")


def score_compare(examples: Sequence[Example], params: Json) -> ToolScore:
    """Gold `{"relation": ..., "perturbation"?: "numeric" | "negation", "aspects"?: {aspect: relation}}`."""
    perturbed: list[bool] = []
    aspect_gold: list[str] = []
    aspect_predicted: list[str | None] = []
    for example in examples:
        relation = as_object(example.output.get("overall")).get("relation")
        if example.gold["relation"] == "contradicts" and example.gold.get("perturbation") in PERTURBATIONS:
            perturbed.append(relation == "contradicts")
        aspects = {str(a.get("aspect")): a.get("relation") for a in as_objects(example.output.get("aspects"))}
        for aspect, label in example.gold.get("aspects", {}).items():
            aspect_gold.append(label)
            got = aspects.get(aspect)
            aspect_predicted.append(got if isinstance(got, str) else None)
    return ToolScore(
        "jev_compare",
        "perturbed_contradiction_recall",
        len(examples),
        {
            "perturbed_contradiction_recall": ratio(sum(perturbed), len(perturbed)),
            "aspect_macro_f1": macro_f1(aspect_gold, aspect_predicted, COMPARE_RELATIONS),
        },
    )


# --- jev_extract ------------------------------------------------------------------------------------


def _judge_extract(example: Example) -> Iterator[Judgment]:
    """`predicted` is the extracted value; None is both "not found" and "no decision"."""
    results = by_id(example.output, "results")
    for field_id, expected in example.gold["fields"].items():
        result = results.get(field_id, {})
        yield Judgment(
            f"{example.id}/{field_id}",
            expected,
            result.get("value"),
            as_number(result.get("top_probability")),
            result.get("status") == "auto",
        )


def score_extract(examples: Sequence[Example], params: Json) -> ToolScore:
    """Gold `{"fields": {field_id: value | null}}`; null means the document has no such value.

    `hallucinated_values` is a hard count of extracted values absent from the input document; the
    target is exactly 0. The result does not list the regex candidates, so this is a document check.
    """
    matches: list[bool] = []
    hallucinated = 0
    absent_gold: list[bool] = []
    not_found: list[bool] = []
    for example in examples:
        document = str(example.input["document"])
        results = by_id(example.output, "results")
        for judgment, field_id in zip(_judge_extract(example), example.gold["fields"], strict=True):
            value = judgment.predicted
            matches.append(judgment.correct)
            if value is not None and str(value) not in document:
                hallucinated += 1
            absent_gold.append(judgment.gold is None)
            not_found.append(results.get(field_id, {}).get("status") == "not_found")
    return ToolScore(
        "jev_extract",
        "exact_match",
        len(matches),
        {
            "exact_match": ratio(sum(matches), len(matches)),
            "hallucinated_values": hallucinated,
            "not_found_precision": precision(absent_gold, not_found),
            "not_found_recall": recall(absent_gold, not_found),
        },
    )


# --- jev_review -------------------------------------------------------------------------------------


def _judge_review(example: Example) -> Iterator[Judgment]:
    score = as_number(example.output.get("safe_to_apply"))
    gold = "defective" if example.gold["defective"] else "safe"
    yield Judgment(example.id, gold, None if score is None else "safe", score, example.output.get("action") == "auto")


def score_review(examples: Sequence[Example], params: Json) -> ToolScore:
    """Gold `{"defective": bool, "severe"?: bool}` (severe implies defective)."""
    auto_defective = [bool(e.gold["defective"]) for e in examples if e.output.get("action") == "auto"]
    scored = [
        (s, not e.gold["defective"]) for e in examples if (s := as_number(e.output.get("safe_to_apply"))) is not None
    ]
    severe = [e.output.get("action") != "auto" for e in examples if e.gold.get("severe", False)]
    return ToolScore(
        "jev_review",
        "p_defective_given_auto",
        len(examples),
        {
            "p_defective_given_auto": ratio(sum(auto_defective), len(auto_defective)),
            "auto": len(auto_defective),
            "safe_to_apply_auroc": auroc([s for s, _ in scored], [ok for _, ok in scored]),
            "severe_defect_recall": ratio(sum(severe), len(severe)),
            "invalid": len(examples) - len(scored),
        },
    )


# --- jev_gate ---------------------------------------------------------------------------------------

GATE_CONFIDENCE = 0.95


def _judge_gate(example: Example) -> Iterator[Judgment]:
    action = example.output.get("action")
    predicted = ("safe" if action == "auto" else "unsafe") if isinstance(action, str) else None
    yield Judgment(example.id, "safe" if example.gold["safe"] else "unsafe", predicted, None, action == "auto")


def score_gate(examples: Sequence[Example], params: Json) -> ToolScore:
    """Gold `{"safe": bool, "claims"?: [verdict per claim, in order], "reason_codes"?: [code]}`.

    False-AUTO rate is P(not safe | AUTO); the 1.0 gate accepts it on its upper bound, reported here as
    the one-sided 95% Clopper-Pearson bound.
    """
    auto_unsafe = [not e.gold["safe"] for e in examples if e.output.get("action") == "auto"]
    claim_gold: list[str] = []
    claim_predicted: list[str | None] = []
    codes_gold: list[object] = []
    codes_predicted: list[object] = []
    for example in examples:
        results = as_objects(as_object(example.output.get("verification")).get("results"))
        for index, label in enumerate(example.gold.get("claims", [])):
            verdict = results[index].get("verdict") if index < len(results) else None
            claim_gold.append(label)
            claim_predicted.append(verdict if isinstance(verdict, str) else None)
        if "reason_codes" in example.gold:
            codes_gold.append(frozenset(example.gold["reason_codes"]))
            raw = example.output.get("reason_codes")
            codes_predicted.append(frozenset(cast(list[object], raw)) if isinstance(raw, list) else None)
    errors, n = sum(auto_unsafe), len(auto_unsafe)
    return ToolScore(
        "jev_gate",
        "false_auto_rate",
        len(examples),
        {
            "false_auto_rate": ratio(errors, n),
            "false_auto_upper_bound": clopper_pearson_upper(errors, n, GATE_CONFIDENCE),
            "auto": n,
            "claim_macro_f1": macro_f1(claim_gold, claim_predicted, VERIFY_VERDICTS),
            "reason_code_accuracy": accuracy(codes_gold, codes_predicted),
        },
    )


# --- jev_score --------------------------------------------------------------------------------------


def _judge_score(example: Example) -> Iterator[Judgment]:
    nearest = example.output.get("nearest_level") if example.output.get("status") == "ok" else None
    yield Judgment(
        example.id,
        example.gold["level"],
        nearest if isinstance(nearest, int) and not isinstance(nearest, bool) else None,
        None,  # no single-score AUTO decision: the caller thresholds the level in code (ADR-0048)
        False,
    )


def score_jev_score(examples: Sequence[Example], params: Json) -> ToolScore:
    """Gold `{"level": int}` — the 0-based rubric level that is right.

    The primary metric is nearest-level accuracy. The fractional `score` feeds the level MAE and
    the within-one rate, and the mass the distribution puts on the gold level is the calibration
    signal. A row with no grade counts in `invalid` and stays out of every score-based metric.
    """
    gold: list[object] = []
    predicted: list[object] = []
    absolute: list[float] = []
    within_one = 0
    gold_mass: list[float] = []
    for example in examples:
        for judgment in _judge_score(example):
            if judgment.predicted is None:
                continue
            score = as_number(example.output.get("score"))
            if score is None:
                continue
            level = int(cast(int, judgment.gold))
            gold.append(judgment.gold)
            predicted.append(judgment.predicted)
            absolute.append(abs(score - float(level)))
            within_one += abs(cast(int, judgment.predicted) - level) <= 1
            mass = as_number(as_object(example.output.get("probabilities")).get(str(level)))
            if mass is not None:
                gold_mass.append(mass)
    valid = len(gold)
    return ToolScore(
        "jev_score",
        "nearest_level_accuracy",
        len(examples),
        {
            "nearest_level_accuracy": accuracy(gold, predicted),
            "level_mae": mean(absolute),
            "within_one_rate": ratio(within_one, valid),
            "gold_level_probability": mean(gold_mass),
            "invalid": len(examples) - valid,
        },
    )


Scorer = Callable[[Sequence[Example], Json], ToolScore]

SCORERS: dict[str, Scorer] = {
    "jev_verify": score_verify,
    "jev_screen": score_screen,
    "jev_find": score_find,
    "jev_classify": score_classify,
    "jev_decide": score_decide,
    "jev_rerank": score_rerank,
    "jev_compare": score_compare,
    "jev_extract": score_extract,
    "jev_review": score_review,
    "jev_gate": score_gate,
    "jev_score": score_jev_score,
}
"""Snapshot order, one scorer per tool; ADR-0048 extensions follow the snapshot ten."""

_JUDGES: dict[str, Callable[[Example], Iterator[Judgment]]] = {
    "jev_verify": _judge_verify,
    "jev_screen": _judge_screen,
    "jev_find": _judge_find,
    "jev_classify": _judge_classify,
    "jev_decide": _judge_decide,
    "jev_rerank": _judge_rerank,
    "jev_compare": _judge_compare,
    "jev_extract": _judge_extract,
    "jev_review": _judge_review,
    "jev_gate": _judge_gate,
    "jev_score": _judge_score,
}


def judgments(tool: str, examples: Sequence[Example]) -> list[Judgment]:
    """Every judged row of `examples`, in example then gold order."""
    return [judgment for example in examples for judgment in _JUDGES[tool](example)]
