"""Triplet analysis over recorded runs. Only items with A, B, and C are summarized.

A server failure is not an arm result and cannot complete a triplet. Arm C's use-gate miss voids its
answer. Arm B's "did not call Jev" does not: the answer is still scored (ADR-0036). `per_tool` adds
each tool's own L3 scorer over the wrapped answers of labeled items. Wall time is the per-arm median,
p90, and p95, plus the paired differences B minus A and C minus A.
"""

import statistics
from collections.abc import Iterable, Mapping, Sequence
from typing import Any, cast

from evals.bench import answer, gate
from evals.bench.items import Item
from evals.bench.ledger import ARMS
from evals.bench.stats import percentile, sign_test
from evals.scorers.fields import Json
from evals.scorers.tools import SCORERS, Example

Record = Mapping[str, Any]
Triplet = tuple[Record, Record, Record]


def complete_triplets(records: Iterable[Record]) -> dict[str, Triplet]:
    """`{item: (A, B, C)}` for items whose three arms are recorded and none is a server failure."""
    by_item: dict[str, dict[str, Record]] = {}
    for record in records:
        if record.get("server_failure"):
            continue
        by_item.setdefault(str(record["item"]), {})[str(record["arm"])] = record
    return {
        item: (arms["A"], arms["B"], arms["C"])
        for item, arms in by_item.items()
        if set(ARMS) <= set(arms) and not any(arms[arm].get("server_failure") for arm in ARMS)
    }


def _spread(values: list[float]) -> dict[str, float | None]:
    if not values:
        return {"median": None, "p90": None, "p95": None}
    return {
        "median": statistics.median(values),
        "p90": percentile(values, 90),
        "p95": percentile(values, 95),
    }


def called_jev(record: Record) -> bool:
    """True when the run invoked a Jev tool. Not calling, and arm A, are false."""
    if record.get("arm") == "A" or record.get("gate") in ("did not call Jev", "no Jev call"):
        return False
    if record.get("gate") == "called Jev, no answer":
        return True
    calls = cast(list[Mapping[str, Any]], record.get("jev_calls") or [])
    if any(call.get("tool") for call in calls):
        return True
    return record.get("gate") is None and bool(calls)


def headline(triplets: Mapping[str, Triplet]) -> dict[str, Any]:
    """Wall-time spread per arm and the paired differences. Accuracy is not computed here."""
    columns = [[triplet[index] for triplet in triplets.values()] for index in range(3)]
    walls = [[float(record["wall_s"]) for record in column] for column in columns]
    result: dict[str, Any] = {"triplets": len(triplets)}
    for arm, samples in zip(ARMS, walls, strict=True):
        spread = _spread(samples)
        result[f"wall_{arm}_median_s"] = spread["median"]
        result[f"wall_{arm}_p90_s"] = spread["p90"]
        result[f"wall_{arm}_p95_s"] = spread["p95"]
        result[f"jev_usd_{arm}"] = sum(float(record.get("jev_cost_usd") or 0) for record in columns[ARMS.index(arm)])
        called = sum(called_jev(record) for record in columns[ARMS.index(arm)])
        result[f"called_jev_{arm}"] = called
        result[f"called_jev_share_{arm}"] = called / len(columns[0]) if columns[0] else None
    if walls[0]:
        for label, other in (("b_minus_a", walls[1]), ("c_minus_a", walls[2])):
            differences = [b - a for a, b in zip(walls[0], other, strict=True)]
            spread = _spread(differences)
            result[f"wall_{label}_median_s"] = spread["median"]
            result[f"wall_{label}_p90_s"] = spread["p90"]
            result[f"wall_{label}_p95_s"] = spread["p95"]
            result[f"wall_{label}_sign_p"] = sign_test(differences)
    return result


SCORER_PARAMS: dict[str, Json] = {"jev_screen": {"max_false_block_rate": 0.0}}
"""Screen's scorer requires a false-block rate, but agent answers carry no injection probability, so its
primary metric is null whatever the rate: this one is a placeholder."""


def _recorded_answer(record: Record) -> answer.Answer | None:
    """The parsed answer, or None when the run cannot be scored.

    Arm C's use-gate miss voids the answer. Arm B's "did not call Jev" and "called Jev, no answer"
    do not. A wrong Jev model voids whichever arm received it.
    """
    forced_miss = record.get("arm") == "C" and record.get("gate") is not None
    failed = record["status"] != "ok" or forced_miss or record.get("cross_check") is not None
    if failed or gate.wrong_models(record.get("jev_calls") or []):
        return None
    given = record["answer"]
    return tuple(cast(list[str], given)) if isinstance(given, list) else cast(str | None, given)


def per_tool(triplets: Mapping[str, Triplet], items: Mapping[str, Item]) -> dict[str, dict[str, Any]]:
    """`{tool: {arm: {"primary", "n", "metrics"}}}` over labeled items; draft items have no gold and are left out.

    Every triplet's item must be in `items`: a record for an unknown item is a KeyError, not a skip.
    """
    examples: dict[str, dict[str, list[Example]]] = {}
    for item_id, triplet in triplets.items():
        item = items[item_id]
        if not item.gold:
            continue
        for record in triplet:
            wrapped = answer.wrap(item, _recorded_answer(record))
            example = Example(item.id, item.family, item.input, item.gold, wrapped)
            examples.setdefault(item.tool, {}).setdefault(str(record["arm"]), []).append(example)
    breakdown: dict[str, dict[str, Any]] = {}
    for tool, arms in examples.items():
        scores = {arm: SCORERS[tool](runs, SCORER_PARAMS.get(tool, {})) for arm, runs in sorted(arms.items())}
        breakdown[tool] = {arm: {"primary": s.primary, "n": s.n, "metrics": s.metrics} for arm, s in scores.items()}
    return breakdown


def early_stop(
    first: Sequence[Triplet],
    *,
    total_runs: int,
    jev_cost: Mapping[str, float],
    budget: float,
    max_failed: int,
) -> str | None:
    """The compliance and Jev-spend stops over the first complete triplets, or None to go on.

    Compliance counts arm C only. Arm B not calling Jev is a result, not a miss. The projection is
    Jev spend (the values in `jev_cost`) across `total_runs`, not agent spend.
    """
    failed = sum(triplet[2]["gate"] is not None for triplet in first)
    if failed >= max_failed:
        return f"compliance stop: {failed} of the first {len(first)} C runs failed the use gate"
    spent = sum(jev_cost[str(record["run_id"])] for triplet in first for record in triplet)
    done = 3 * len(first)
    projected = spent / done * total_runs
    if projected > budget:
        return f"cost stop: {spent:.4f} Jev USD over {done} runs projects {projected:.2f} > {budget:.2f}"
    return None
