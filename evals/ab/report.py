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
    "**Success:** ADR-0073. Hidden acceptance tests all pass, no pre-existing test regresses, every "
    "pre-existing test is unchanged in content (new test functions and new test files are allowed; "
    "modifying, deleting, skipping, or weakening a pre-existing test line fails the run), every added "
    "test passes, and the stated decision matches gold when the task has gold. **Old-rule success:** "
    "the pre-ADR-0073 grader: acceptance passes, no regressions, and no pre-existing test file's bytes "
    "changed. A correct fix plus an added test method fails the old rule and passes the new one. "
    "Records graded before ADR-0073 store only the old rule in `success`. **Final tests passed:** "
    "every pristine graded test passed; added tests are separate.",
    "**Added tests:** count, file, name, pass/fail, and relevance. Relevant means the added test's file "
    "imports the task's `target_module`. A failing or irrelevant added test fails the run (its own "
    "failure category); relevance is the ask's own requirement, and in the recorded D3 runs every "
    "added test was relevant (52 of 52), so the clause changed no grade there.",
    "**Unnecessary Jev call:** every proxy-logged Jev call on a control task (the code or tests already "
    "determine the answer), or a later call of the same tool with the same arguments as an earlier call "
    "in that run. The repeat count cannot exceed the proxy log.",
    "**Jev changed the decision:** the with-Jev run's final decision differs from its paired without-Jev "
    "run and equals the option Jev's last non-error result selects — the one answer field of the tool's "
    "JSON document (`top[0].id` for find, `results[0].verdict` for verify, and so on), mapped to an option "
    "id by `task.json`'s `jev_verdict_options` where the tool answers with a verdict word. If that field "
    "names no task option, the change is not observed. The paired comparison is a **B-versus-paired-A "
    "proxy**, not within-run causality: the two arms are independent stochastic trajectories. **The "
    "change was correct** when that final decision equals gold.",
    "**Jev round trip:** the proxy's per-call `ms`, which includes the server's local work and so bounds "
    "provider latency from above. **Run wall** is `wall_s`.",
    "**Failure category:** why a failed run failed, first match: timeout, agent error, acceptance miss, "
    "regression, pre-existing test altered, added test failing, wrong decision, Jev error, harness error, "
    "other. A correct run has none — except that a confined run whose commands used any path outside "
    "its task workdir carries **out-of-task exploration** instead: the evidence paths are recorded, the "
    "run is still graded, and it never stops the study by itself. Only three things void a confined "
    "study: a secret-scan hit, host material in a tool result, or a reach for fixture material (task "
    "metadata, gold, hidden acceptance tests, distractor solutions) outside the task workdir.",
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


def _round_trip(runs: Sequence[Record]) -> str:
    samples = [
        float(call["ms"])
        for run in runs
        for call in cast(Sequence[Mapping[str, Any]], run.get("jev_call_log") or [])
        if isinstance(call.get("ms"), int | float)
    ]
    return distribution(samples) if samples else "not recorded"


def _flag_count(runs: Sequence[Record], key: str) -> str:
    if not any(key in run for run in runs):
        return "not recorded"
    return f"{sum(bool(run.get(key)) for run in runs)}/{len(runs)}"


def _categories(runs: Sequence[Record]) -> str:
    failed = [run for run in runs if not run["success"]]
    if not failed:
        return "none"
    if not any("failure_category" in run for run in failed):
        return "not recorded"
    counts = Counter(str(run.get("failure_category") or "other") for run in failed)
    return "; ".join(f"{name} {count}" for name, count in counts.most_common())


def jev_changed_decision(without: Record, with_jev: Record) -> bool | None:
    """True when B's decision differs from A's and equals Jev's observed option. None if unobserved."""
    answer = with_jev.get("jev_answer")
    chosen = with_jev.get("decision")
    other = without.get("decision")
    if not isinstance(answer, str) or not isinstance(chosen, str) or not isinstance(other, str):
        return None
    return chosen != other and chosen == answer


def arm_summary(runs: Sequence[Record]) -> dict[str, str]:
    solved = [r for r in runs if r["success"]]
    hours = sum(float(r["wall_s"]) for r in runs) / 3600
    branch_runs = [r for r in runs if r["wrong_branches"] is not None]
    judged = [r for r in runs if r["decision_correct"] is not None]
    summary = {
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
        "unnecessary Jev calls (total)": str(sum(int(r.get("unnecessary_jev_calls") or 0) for r in runs)),
        "Jev round trip, ms": _round_trip(runs),
        "old-rule successes": _flag_count(runs, "old_rule_success"),
        "failure categories": _categories(runs),
        "judge accuracy (decision = gold)": (
            f"{sum(bool(r['decision_correct']) for r in judged)}/{len(judged)}" if judged else "no gold decision"
        ),
    }
    if any("exploration" in r for r in runs):
        explored = [r for r in runs if r.get("exploration")]
        clean = [r for r in runs if not r.get("exploration")]
        summary["out-of-task exploration (runs; evidence paths)"] = (
            f"{len(explored)}/{len(runs)}; {sum(len(r['exploration']) for r in explored)} paths"
        )
        summary["tasks solved, exploration runs excluded"] = f"{sum(bool(r['success']) for r in clean)}/{len(clean)}"
    return summary


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
    observed = [item for item in (jev_changed_decision(a, b) for a, b in measured)]
    changed = [item for item in observed if item is True]
    correct = [
        b for (_, b), item in zip(measured, observed, strict=True) if item is True and b.get("decision_correct") is True
    ]
    lines.append(
        f"- Jev changed the decision: {len(changed)} observed changes; "
        f"{len(correct)} of those equal gold; {observed.count(None)} pairs had no Jev option to compare."
    )
    lines.append("- Old-rule vs new-rule successes are the arm rows `old-rule successes` and `tasks solved`.")
    lines.append("- Descriptive only: no significance test, and n is small.")
    return lines


def _invocation(records: Sequence[Record]) -> list[str]:
    """Jev invocation over EVERY reached with-Jev run, not only measured pairs.

    Voided non-callers made the old rate tautologically 100%: a run was only measured when it had
    called. The control-task change (a non-calling control B run is measured) keeps the pair, and
    this line carries the restraint signal for every reached run.
    """
    reached_b = [
        r
        for r in records
        if r["arm"] == "B" and r.get("measurement") not in ("never reached the model", "harness error")
    ]
    if not reached_b:
        return []
    called = [r for r in reached_b if r.get("jev_tool_called")]
    return [
        f"- Jev invocation over all {len(reached_b)} reached with-Jev runs: {len(called)} called "
        f"({len(reached_b) - len(called)} did not); "
        f"{sum(int(r.get('unnecessary_jev_calls') or 0) for r in reached_b)} unnecessary calls."
    ]


def _task_table(measured: Sequence[Pair]) -> list[str]:
    """Old-rule and new-rule success, Jev calls, and failure categories, per task and arm."""
    exploration = any("exploration" in run for pair in measured for run in pair)
    mid = " explored | " if exploration else " "
    lines = [
        f"| task | arm | new-rule success | old-rule success | Jev calls | unnecessary |{mid}failure categories |",
        "|---|" * (8 if exploration else 7),
    ]
    by_task: dict[str, list[Pair]] = {}
    for pair in measured:
        by_task.setdefault(str(pair[0]["task"]), []).append(pair)
    for task_id in sorted(by_task):
        for index, arm in enumerate(ARMS):
            runs = [pair[index] for pair in by_task[task_id]]
            cell = f"{sum(1 for r in runs if r.get('exploration'))}/{len(runs)} | " if exploration else ""
            lines.append(
                f"| {task_id} | {arm} | {sum(bool(r['success']) for r in runs)}/{len(runs)} | "
                f"{_flag_count(runs, 'old_rule_success')} | {sum(int(r['jev_calls']) for r in runs)} | "
                f"{sum(int(r.get('unnecessary_jev_calls') or 0) for r in runs)} | {cell}{_categories(runs)} |"
            )
    return lines


def _rule_comparison(records: Sequence[Record]) -> list[str]:
    """Old-rule vs new-rule counts over every recorded run, not only measured pairs.

    A pair whose with-Jev arm never called Jev is not an outcome, but it is still a grader
    result. The D2 effect is a run the old byte rule fails and the new rule passes.
    """
    if not any("old_rule_success" in run for run in records):
        return []
    lines = [
        "### Old rule vs new rule, all recorded runs",
        "",
        "Not limited to measured pairs. New-rule success allows added tests and requires the decision "
        "to match gold. Old-rule success fails any byte change to a pre-existing test file and does not "
        "read the decision. A save is a run the old rule fails and the new rule passes.",
        "",
        "| task | arm | runs | new-rule | old-rule | saves |",
        "|---|---|---|---|---|---|",
    ]
    by_task: dict[str, list[Record]] = {}
    for run in records:
        by_task.setdefault(str(run["task"]), []).append(run)
    for task_id in sorted(by_task):
        for arm in ARMS:
            runs = [run for run in by_task[task_id] if run["arm"] == arm]
            if not runs:
                continue
            saves = sum(bool(run["success"]) and not run.get("old_rule_success") for run in runs)
            lines.append(
                f"| {task_id} | {arm} | {len(runs)} | {sum(bool(run['success']) for run in runs)}/{len(runs)} | "
                f"{sum(bool(run.get('old_rule_success')) for run in runs)}/{len(runs)} | {saves} |"
            )
    saves = sum(bool(run["success"]) and not run.get("old_rule_success") for run in records)
    lines += [
        "",
        f"All recorded runs: new-rule {sum(bool(run['success']) for run in records)}/{len(records)}, "
        f"old-rule {sum(bool(run.get('old_rule_success')) for run in records)}/{len(records)}, saves {saves}.",
        "",
    ]
    return lines


def _runs_table(runs: Sequence[Record]) -> list[str]:
    lines = [
        "| run | measurement | success | tests passed | wall s | tokens | tool calls | Jev calls | test cycles "
        "| wrong branches | decision | failure |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in runs:
        branches = "n/a" if r["wrong_branches"] is None else (", ".join(r["wrong_branches"]) or "0")
        lines.append(
            f"| {r['run_id']} | {r['measurement'] or 'measured'} | {'yes' if r['success'] else 'no'} | "
            f"{'yes' if r['final_tests_passed'] else 'no'} | {float(r['wall_s']):.1f} | {r['tokens']['total']} | "
            f"{r['tool_calls']} | {r['jev_calls']} | {r['test_cycles']} | {branches} | {r['decision'] or 'none'} | "
            f"{r.get('failure_category') or ''} |"
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
            *(f"| {m} | " + " | ".join(summaries[arm].get(m, "") for arm in ARMS) + " |" for m in summaries[ARMS[0]]),
            "",
            "### Paired",
            "",
            *paired_lines(measured),
            "",
            *_invocation(records),
            "",
            "### Per task",
            "",
            *_task_table(measured),
            "",
        ]
    lines += [*_rule_comparison(records), "### Runs", "", *_runs_table(sorted(records, key=lambda r: r["run_id"])), ""]
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
