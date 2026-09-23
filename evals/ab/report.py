"""Render the outcome study's run records into evals/reports/agent-outcomes.md. Pure: records in, markdown out.

Every number comes from measured pairs only: the same agent, task and repeat, with both arms measured
(`evals.ab.outcomes.measurement`). An arm-B run that never used Jev drops its whole pair, so both arms
are always summarized over the same tasks. With no measured pair the report says "not measured" and
prints no outcome numbers and no difference. It never computes an item rate: that measures the
evaluator, not the agent.
"""

import statistics
from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any, cast

from evals.ab.arms import ARM_LABELS, ARMS

Record = Mapping[str, Any]
Pair = tuple[Record, Record]
"""(arm A, arm B)."""

NOT_MEASURED = "not measured"
HISTORY = (
    "History, not this study: `p8-pilot.md` (2026-09-21; no run called a Jev tool) and `bench150.md` "
    "(accuracy unscored, item rate is evaluator overhead) measured something else and are kept as recorded."
)
DEFINITIONS = [
    "**Measured run:** reached the model (at least one frontier call produced output). A with-Jev run also "
    "needs a Jev answer from the pinned model through the MCP server; the proxy log, not the agent's text, "
    "decides. A run that reached the model and then failed is a measured failure, never a fast completion.",
    "**Measured pair:** both arms of one task and repeat measured. Only measured pairs are summarized.",
    "**Success:** the existing grader: every hidden acceptance test passes, no pre-existing test regresses, "
    "no pre-existing test file changed. **Final tests passed:** every graded test passed.",
    "**Correct solutions per hour:** successes divided by the summed wall time of every measured run of the "
    "arm, failures included.",
    "**Test cycles:** shell calls that run `unittest` or `pytest`. **Retries:** test cycles after the first.",
    "**Wrong branch:** a non-gold option whose declared signature appears in what the agent wrote or ran; a "
    "heuristic lower bound, counted only on tasks that declare signatures.",
    "**Judge accuracy:** the agent's stated decision against the task's gold decision; reported only for "
    "tasks with gold.",
]


def pairs(records: Sequence[Record]) -> dict[tuple[str, str, int], dict[str, Record]]:
    grouped: dict[tuple[str, str, int], dict[str, Record]] = {}
    for record in records:
        grouped.setdefault((record["agent"], record["task"], int(record["repeat"])), {})[record["arm"]] = record
    return grouped


def pair_exclusion(arms: Mapping[str, Record]) -> str | None:
    """None when the pair is measured, else why it is not evidence."""
    missing = [arm for arm in ARMS if arm not in arms]
    if missing:
        return f"incomplete: no {', '.join(missing)} run"
    reasons = [f"{arm}: {arms[arm]['measurement']}" for arm in ARMS if arms[arm]["measurement"] is not None]
    return "; ".join(reasons) or None


def measured_pairs(records: Sequence[Record]) -> dict[tuple[str, str, int], Pair]:
    return {key: (arms["A"], arms["B"]) for key, arms in pairs(records).items() if pair_exclusion(arms) is None}


def _num(value: float) -> str:
    return f"{value:.1f}" if abs(value) < 1000 else f"{value:.0f}"


def distribution(values: Sequence[float]) -> str:
    if not values:
        return "none"
    ordered = sorted(values)
    listed = f" [{', '.join(_num(v) for v in ordered)}]" if len(ordered) <= 10 else ""
    return (
        f"median {_num(statistics.median(ordered))} (n={len(ordered)}, min {_num(ordered[0])}, "
        f"max {_num(ordered[-1])}){listed}"
    )


def _per_solved(total: float, solved: int) -> str:
    return _num(total / solved) if solved else "n/a (0 solved)"


def arm_summary(runs: Sequence[Record]) -> dict[str, str]:
    solved = [r for r in runs if r["success"]]
    hours = sum(float(r["wall_s"]) for r in runs) / 3600
    branch_runs = [r for r in runs if r["wrong_branches"] is not None]
    judged = [r for r in runs if r["decision_correct"] is not None]
    return {
        "tasks solved": f"{len(solved)}/{len(runs)}",
        "time to a correct solution, s": distribution([float(r["wall_s"]) for r in solved]),
        "correct solutions per hour": f"{len(solved) / hours:.2f}" if hours else "n/a",
        "final tests passed": f"{sum(bool(r['final_tests_passed']) for r in runs)}/{len(runs)}",
        "wrong branches (runs with one / runs on tasks with signatures; total)": (
            f"{sum(bool(r['wrong_branches']) for r in branch_runs)}/{len(branch_runs)}; "
            f"{sum(len(r['wrong_branches']) for r in branch_runs)}"
        )
        if branch_runs
        else "no task declares signatures",
        "test cycles per run": distribution([float(r["test_cycles"]) for r in runs]),
        "retries (total)": str(sum(int(r["retries"]) for r in runs)),
        "tool calls per solved task": _per_solved(sum(int(r["tool_calls"]) for r in runs), len(solved)),
        "tokens per solved task (context + output)": _per_solved(
            sum(int(r["tokens"]["total"]) for r in runs), len(solved)
        ),
        "Jev calls (total; runs with one)": (
            f"{sum(int(r['jev_calls']) for r in runs)}; {sum(bool(r['jev_tool_called']) for r in runs)}/{len(runs)}"
        ),
        "judge accuracy (decision = gold)": (
            f"{sum(bool(r['decision_correct']) for r in judged)}/{len(judged)}" if judged else "no gold decision"
        ),
    }


def paired_lines(measured: Sequence[Pair]) -> list[str]:
    both = [(a, b) for a, b in measured if a["success"] and b["success"]]
    only_a = sum(bool(a["success"]) and not b["success"] for a, b in measured)
    only_b = sum(bool(b["success"]) and not a["success"] for a, b in measured)
    lines = [
        f"- Pairs: {len(measured)}. Solved by both: {len(both)}; only without Jev: {only_a}; only with Jev: {only_b}; "
        f"neither: {len(measured) - len(both) - only_a - only_b}.",
    ]
    if both:
        diffs = [float(b["wall_s"]) - float(a["wall_s"]) for a, b in both]
        lines.append(f"- Wall time, with minus without, pairs both solved (s): {distribution(diffs)}.")
    lines.append("- Descriptive only: no significance test, and n is small.")
    return lines


def _runs_table(runs: Sequence[Record]) -> list[str]:
    lines = [
        "| run | measurement | success | tests passed | wall s | tokens | tool calls | Jev calls | test cycles "
        "| wrong branches | decision |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in runs:
        branches = "n/a" if r["wrong_branches"] is None else (", ".join(r["wrong_branches"]) or "0")
        lines.append(
            f"| {r['run_id']} | {r['measurement'] or 'measured'} | {'yes' if r['success'] else 'no'} | "
            f"{'yes' if r['final_tests_passed'] else 'no'} | {float(r['wall_s']):.1f} | {r['tokens']['total']} | "
            f"{r['tool_calls']} | {r['jev_calls']} | {r['test_cycles']} | {branches} | {r['decision'] or 'none'} |"
        )
    return lines


def agent_section(agent: str, records: Sequence[Record], meta: Mapping[str, Any]) -> list[str]:
    grouped = {key: arms for key, arms in pairs(records).items() if key[0] == agent}
    measured = [(arms["A"], arms["B"]) for arms in grouped.values() if pair_exclusion(arms) is None]
    excluded = {key: pair_exclusion(arms) for key, arms in grouped.items() if pair_exclusion(arms) is not None}
    lines = [f"## {agent}", ""]
    if meta:
        lines += [
            f"- **agent:** {meta.get('agent_version') or agent}",
            f"- **Jev revision:** `{meta.get('jev_revision')}`; **fixture sha256:** `{meta.get('fixture_sha256')}`",
            f"- **hardware:** {meta.get('hardware')}",
            *(f"- **{k}:** {v}" for k, v in cast(Mapping[str, str], meta.get("held_constant") or {}).items()),
            "",
        ]
    lines += [f"Runs recorded: {len(records)}. Pairs: {len(grouped)}. Measured pairs: {len(measured)}.", ""]
    if excluded:
        counts = Counter(str(reason) for reason in excluded.values())
        lines += ["Excluded pairs (not evidence of any Jev effect):", ""]
        lines += [f"- {count} x {reason}" for reason, count in counts.most_common()]
        lines.append("")
    if not measured:
        lines += [
            f"**Not measured.** No pair of {agent} runs had both arms measured, so this study has no outcome "
            "numbers and no difference between the arms for this agent.",
            "",
        ]
    else:
        by_arm = {arm: [pair[i] for pair in measured] for i, arm in enumerate(ARMS)}
        summaries = {arm: arm_summary(by_arm[arm]) for arm in ARMS}
        lines += [
            f"### Outcomes over {len(measured)} measured pairs",
            "",
            "| measure | " + " | ".join(f"{arm}: {ARM_LABELS[arm]}" for arm in ARMS) + " |",
            "|---|" + "---|" * len(ARMS),
            *(f"| {m} | " + " | ".join(summaries[arm][m] for arm in ARMS) + " |" for m in summaries[ARMS[0]]),
            "",
            "### Paired",
            "",
            *paired_lines(measured),
            "",
        ]
    lines += ["### Runs", "", *_runs_table(sorted(records, key=lambda r: r["run_id"])), ""]
    return lines


def render(records: Sequence[Record], meta: Mapping[str, Mapping[str, Any]]) -> str:
    agents = sorted({r["agent"] for r in records} | set(meta))
    lines = [
        "# Agent outcome study: does Jev improve a coding agent while it uses the Jev MCP tools?",
        "",
        "Same agent, same task, same repository revision, with and without the Jev MCP server; each task hinges "
        "on a judgment a Jev tool is for, and the with-Jev arm is told to use that tool. Raw per-run records: "
        "`evals/reports/agent-outcomes/` (gitignored).",
        "",
        HISTORY,
        "",
    ]
    if not records:
        lines += [f"**{NOT_MEASURED.capitalize()}.** No runs are recorded, so there are no outcome numbers.", ""]
    for agent in agents:
        lines += agent_section(agent, [r for r in records if r["agent"] == agent], meta.get(agent, {}))
    lines += ["## Definitions", "", *(f"- {d}" for d in DEFINITIONS)]
    return "\n".join(lines).rstrip() + "\n"
