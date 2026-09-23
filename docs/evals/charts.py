"""Render the recorded eval results as static SVG charts.

Reads only the recorded data beside this script (no network, no provider, no repo imports):

- `live/live-*.report.json` — the L3 score reports (`evals.runners.score --report`).
- `agent-outcomes/<agent>/meta.json` and `agent-outcomes/<agent>/*/result.json` — the minimal
  raw outcome-study records (the same files `evals.ab.run --report-only` renders from).

Writes `charts/*.svg`. Run from the repo root or anywhere:

    uv run python docs/evals/charts.py

Reporting rules (evals/ab/report.py): every number comes from measured pairs only — the same
agent, task and repeat with both arms measured; a pair whose with-Jev run never produced a Jev
answer is excluded and only counted. No item rates: they measure the evaluator, not the agent.
"""

import json
import statistics
from collections.abc import Mapping, Sequence
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "charts"

BAR_A = "#94a3b8"  # arm A: without Jev
BAR_B = "#ff8a3d"  # arm B: with Jev
TEXT = "#24292f"
MUTED = "#57606a"
GRID = "#d0d7de"

ARMS = ("A", "B")
ARM_LABELS = {"A": "without Jev", "B": "with Jev"}


# ---------- recorded data ----------


def load_records(agent_dir: Path) -> list[dict]:
    return [json.loads(p.read_text()) for p in sorted(agent_dir.glob("*/result.json"))]


def load_meta(agent_dir: Path) -> dict:
    path = agent_dir / "meta.json"
    return json.loads(path.read_text()) if path.exists() else {}


def pair_exclusion(arms: Mapping[str, dict]) -> str | None:
    """None when both arms are present and measured, else why the pair is not evidence."""
    missing = [arm for arm in ARMS if arm not in arms]
    if missing:
        return f"incomplete: no {', '.join(missing)} run"
    reasons = [f"{arm}: {arms[arm]['measurement']}" for arm in ARMS if arms[arm]["measurement"] is not None]
    return "; ".join(reasons) or None


def measured_pairs(records: Sequence[dict]) -> tuple[list[tuple[dict, dict]], list[str]]:
    grouped: dict[tuple[str, str, int], dict[str, dict]] = {}
    for record in records:
        grouped.setdefault((record["agent"], record["task"], int(record["repeat"])), {})[record["arm"]] = record
    measured, excluded = [], []
    for arms in grouped.values():
        reason = pair_exclusion(arms)
        if reason is None:
            measured.append((arms["A"], arms["B"]))
        else:
            excluded.append(reason)
    return measured, excluded


def arm_numbers(runs: Sequence[dict]) -> dict:
    solved = [float(r["wall_s"]) for r in runs if r["success"]]
    judged = [r for r in runs if r["decision_correct"] is not None]
    return {
        "runs": len(runs),
        "solved": len(solved),
        "median_time": statistics.median(solved) if solved else None,
        "tokens_per_solved": (sum(int(r["tokens"]["total"]) for r in runs) / len(solved)) if solved else None,
        "jev_called": sum(bool(r["jev_tool_called"]) for r in runs),
        "judge_correct": sum(bool(r["decision_correct"]) for r in judged),
        "judge_total": len(judged),
    }


def agent_chart_data(agent_dir: Path) -> dict:
    records = load_records(agent_dir)
    measured, excluded = measured_pairs(records)
    by_arm = {arm: [pair[i] for pair in measured] for i, arm in enumerate(ARMS)}
    numbers = {arm: arm_numbers(by_arm[arm]) for arm in ARMS}
    both_solved = [(a, b) for a, b in measured if a["success"] and b["success"]]
    diffs = [float(b["wall_s"]) - float(a["wall_s"]) for a, b in both_solved]
    meta = load_meta(agent_dir)
    return {
        "agent": records[0]["agent"] if records else agent_dir.name,
        "model": meta.get("held_constant", {}).get("model", ""),
        "agent_version": str(meta.get("agent_version", "")),
        "pairs": len(measured) + len(excluded),
        "measured": len(measured),
        "excluded": excluded,
        "numbers": numbers,
        "wall_diff": {
            "n": len(diffs),
            "median": statistics.median(diffs) if diffs else None,
            "min": min(diffs) if diffs else None,
            "max": max(diffs) if diffs else None,
        },
    }


# ---------- SVG helpers ----------


def text(
    x: float, y: float, s: str, *, size: float = 12, fill: str = TEXT, anchor: str = "start", weight: str = "normal"
) -> str:
    return (
        f'<text x="{x:.1f}" y="{y:.1f}" font-family="-apple-system,Segoe UI,Helvetica,Arial,sans-serif" '
        f'font-size="{size}" fill="{fill}" text-anchor="{anchor}" font-weight="{weight}">{s}</text>'
    )


def bar(x: float, y: float, w: float, h: float, fill: str) -> str:
    return f'<rect x="{x:.1f}" y="{y:.1f}" width="{max(w, 0):.1f}" height="{max(h, 0):.1f}" rx="3" fill="{fill}"/>'


def panel(
    x: float,
    y: float,
    w: float,
    h: float,
    title: str,
    values: Sequence[float | None],
    labels: Sequence[str],
    colors: Sequence[str],
    *,
    ymax: float | None = None,
    sub: str = "",
) -> list[str]:
    """One metric panel: bars grow up from a baseline, one bar per arm, value labels on top."""
    out = [text(x, y - 10, title, size=12, weight="600")]
    if sub:
        out.append(text(x, y + h + 14, sub, size=10.5, fill=MUTED))
    top, base = y + 6, y + h - 16
    height = base - top
    scale = ymax if ymax is not None else max((v for v in values if v is not None), default=1.0) or 1.0
    slot = w / len(values)
    bar_w = min(46.0, slot * 0.55)
    for i, (value, label) in enumerate(zip(values, labels, strict=True)):
        cx = x + slot * i + slot / 2
        if value is None:
            out.append(text(cx, base - 4, "n/a", size=11, fill=MUTED, anchor="middle"))
        else:
            bh = height * float(value) / scale
            out.append(bar(cx - bar_w / 2, base - bh, bar_w, bh, colors[i]))
            out.append(text(cx, base - bh - 5, label, size=11, anchor="middle", weight="600"))
    out.append(
        f'<line x1="{x:.1f}" y1="{base:.1f}" x2="{x + w:.1f}" y2="{base:.1f}" stroke="{GRID}" stroke-width="1"/>'
    )
    return out


def svg(doc: list[str], width: int, height: int, title: str, footer: str) -> str:
    body = "\n".join(doc)
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" '
        f'width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" '
        f'aria-label="{title}: {footer}">\n'
        f"{body}\n</svg>\n"
    )


def fmt(value: float | None, digits: int = 1, suffix: str = "") -> str:
    return "n/a" if value is None else f"{value:.{digits}f}{suffix}"


# ---------- charts ----------


def agent_panel_chart(data: Mapping, path: Path) -> None:
    """Three outcome panels over measured pairs plus the paired wall-time difference."""
    numbers = data["numbers"]
    solved_label = {arm: f"{numbers[arm]['solved']}/{numbers[arm]['runs']}" for arm in ARMS}
    max_time = max((numbers[arm]["median_time"] or 0) for arm in ARMS) or 1.0
    max_tokens = max((numbers[arm]["tokens_per_solved"] or 0) for arm in ARMS) or 1.0

    width, height = 900, 240
    doc: list[str] = []
    pw, gap, x0, y0, ph = 200, 24, 20, 46, 130
    doc += [
        text(
            x0,
            24,
            f"{data['agent']} — {data['agent_version']}, model {data['model'].replace('`', '')}",
            size=14,
            weight="700",
        )
    ]
    doc += panel(
        x0,
        y0,
        pw,
        ph,
        "Tasks solved (measured pairs)",
        [numbers[a]["solved"] / numbers[a]["runs"] for a in ARMS],
        [solved_label[a] for a in ARMS],
        [BAR_A, BAR_B],
        ymax=1.0,
    )
    doc += panel(
        x0 + (pw + gap),
        y0,
        pw,
        ph,
        "Median time to correct solution",
        [numbers[a]["median_time"] for a in ARMS],
        [fmt(numbers[a]["median_time"], 1, " s") for a in ARMS],
        [BAR_A, BAR_B],
        ymax=max_time * 1.25,
        sub=f"s, n={numbers['A']['solved']} solved per arm",
    )
    doc += panel(
        x0 + 2 * (pw + gap),
        y0,
        pw,
        ph,
        "Tokens per solved task",
        [numbers[a]["tokens_per_solved"] / 1000 for a in ARMS],
        [fmt(numbers[a]["tokens_per_solved"] / 1000, 1, " k") for a in ARMS],
        [BAR_A, BAR_B],
        ymax=max_tokens * 1.25,
        sub="thousands, context + output",
    )
    diff = data["wall_diff"]
    doc += panel(
        x0 + 3 * (pw + gap),
        y0,
        pw,
        ph,
        "Wall time with-minus-without",
        [diff["median"]],
        [fmt(diff["median"], 1, " s")],
        [BAR_B],
        ymax=(diff["median"] or 0) * 1.3 or 1.0,
        sub=f"median, pairs both solved (n={diff['n']})",
    )
    legend_x = x0 + 4 * (pw + gap)
    doc += [bar(legend_x, y0 + 8, 12, 12, BAR_A), text(legend_x + 17, y0 + 18, "A " + ARM_LABELS["A"], size=11)]
    doc += [bar(legend_x, y0 + 28, 12, 12, BAR_B), text(legend_x + 17, y0 + 38, "B " + ARM_LABELS["B"], size=11)]

    excluded = (
        f"; {len(data['excluded'])} pair excluded ({data['excluded'][0].split(': ', 1)[1] if data['excluded'] else ''})"
        if data["excluded"]
        else ""
    )
    acc = {arm: f"{numbers[arm]['judge_correct']}/{numbers[arm]['judge_total']}" for arm in ARMS}
    footer = (
        f"{data['measured']} of {data['pairs']} pairs measured{excluded}. Judge accuracy (decision = gold): "
        f"A {acc['A']}, B {acc['B']}. Jev called in {numbers['B']['jev_called']}/{numbers['B']['runs']} with-Jev runs. "
        "Descriptive only: n is small, no significance test."
    )
    doc += [text(x0, height - 14, footer, size=11, fill=MUTED)]
    path.write_text(svg(doc, width, height, f"Agent outcomes: {data['agent']}", footer))


def pairs_chart(charts: Sequence[Mapping], path: Path) -> None:
    """Measured versus excluded pairs per agent: the count that bounds every claim."""
    width, height = 900, 190
    x0, y0, ph, pw, gap = 20, 52, 92, 300, 60
    doc = [text(x0, 24, "Measured and excluded pairs per agent", size=14, weight="700")]
    ymax = max(c["pairs"] for c in charts) or 1
    for i, c in enumerate(charts):
        x = x0 + i * (pw + gap)
        scale = ph / (ymax * 1.2)
        base = y0 + ph
        measured_h = c["measured"] * scale
        excluded_h = len(c["excluded"]) * scale
        doc += [
            text(
                x + pw / 2,
                y0 - 8,
                f"{c['agent']} — {c['agent_version'].split()[0] if c['agent_version'] else c['agent']}",
                size=12,
                anchor="middle",
                weight="600",
            )
        ]
        doc += [bar(x + pw / 2 - 55, base - measured_h, 50, measured_h, BAR_A)]
        doc += [
            text(x + pw / 2 - 30, base - measured_h - 5, str(c["measured"]), size=11, anchor="middle", weight="600")
        ]
        if excluded_h:
            doc += [bar(x + pw / 2 + 5, base - excluded_h, 50, excluded_h, "#d29922")]
            doc += [
                text(
                    x + pw / 2 + 30,
                    base - excluded_h - 5,
                    str(len(c["excluded"])),
                    size=11,
                    anchor="middle",
                    weight="600",
                )
            ]
        else:
            doc += [text(x + pw / 2 + 30, base - 4, "0", size=11, anchor="middle", fill=MUTED)]
        doc += [
            f'<line x1="{x:.1f}" y1="{base:.1f}" x2="{x + pw:.1f}" y2="{base:.1f}" stroke="{GRID}" stroke-width="1"/>'
        ]
        doc += [text(x + pw / 2 - 30, base + 16, "measured", size=10.5, anchor="middle", fill=MUTED)]
        doc += [text(x + pw / 2 + 30, base + 16, "excluded", size=10.5, anchor="middle", fill=MUTED)]
    doc += [
        text(
            x0,
            height - 10,
            "A pair is evidence only when both arms are measured; an excluded pair contributes no number "
            "anywhere in this report.",
            size=11,
            fill=MUTED,
        )
    ]
    path.write_text(svg(doc, width, height, "Measured and excluded pairs", "measured versus excluded pairs per agent"))


def live_chart(reports: Mapping[str, dict], path: Path) -> None:
    """L3 live judgment quality: probability-scale metrics per tool from the recorded score reports."""
    panels = [
        ("jev_classify", ["selective_accuracy_auto", "macro_f1", "micro_f1", "auto_coverage"]),
        ("jev_verify", ["contradiction_recall", "selective_accuracy", "macro_f1", "auto_coverage"]),
    ]
    width, height = 900, 230
    x0, y0, pw, ph, gap = 20, 52, 200, 120, 60
    doc = [text(x0, 24, "L3 live judgment quality (jev-1.13.0 through TypeSafe, 2026-09-23)", size=14, weight="700")]
    for i, (tool, metrics) in enumerate(panels):
        report = reports[f"live-{tool.removeprefix('jev_')}"]
        x = x0 + i * (pw + gap)
        values = [float(report["metrics"][m]) for m in metrics]
        doc += [text(x, y0 - 8, f"{tool}  (n={report['n']} scored answers)", size=12, anchor="middle", weight="600")]
        for j, (metric, value) in enumerate(zip(metrics, values, strict=True)):
            bx = x + j * (pw / len(metrics))
            bh = ph * min(value, 1.0)
            doc += [bar(bx + pw / len(metrics) / 2 - 22, y0 + ph - bh, 44, bh, BAR_B if value >= 0.999 else "#d29922")]
            doc += [
                text(
                    bx + pw / len(metrics) / 2,
                    y0 + ph - bh - 5,
                    f"{value:.2f}",
                    size=10.5,
                    anchor="middle",
                    weight="600",
                )
            ]
            words = metric.split("_")
            doc += [text(bx + pw / len(metrics) / 2, y0 + ph + 14, words[0], size=9.5, anchor="middle", fill=MUTED)]
            if len(words) > 1:
                doc += [
                    text(
                        bx + pw / len(metrics) / 2,
                        y0 + ph + 25,
                        " ".join(words[1:]),
                        size=9.5,
                        anchor="middle",
                        fill=MUTED,
                    )
                ]
        grid = f'<line x1="{x:.1f}" y1="{y0 + ph:.1f}" x2="{x + pw:.1f}" y2="{y0 + ph:.1f}" stroke="{GRID}"/>'
        doc += [grid]
    note = (
        "Plumbing and quality on the synthetic live datasets (3 cases per tool); "
        "bars at 1.00 mean every scored answer met the metric."
    )
    doc += [text(x0, height - 8, note, size=11, fill=MUTED)]
    path.write_text(svg(doc, width, height, "L3 live judgment quality", "live metrics for classify and verify at 1.00"))


def main() -> None:
    OUT.mkdir(exist_ok=True)
    reports = {
        p.stem.replace(".report", ""): json.loads(p.read_text()) for p in sorted((HERE / "live").glob("*.report.json"))
    }
    if reports:
        live_chart(reports, OUT / "live-l3.svg")
    charts = []
    for agent_dir in sorted((HERE / "agent-outcomes").iterdir()):
        if not agent_dir.is_dir():
            continue
        data = agent_chart_data(agent_dir)
        charts.append(data)
        agent_panel_chart(data, OUT / f"agent-outcomes-{data['agent']}.svg")
    if charts:
        pairs_chart(charts, OUT / "agent-outcomes-pairs.svg")
    for chart in sorted(OUT.glob("*.svg")):
        print(chart.relative_to(HERE))


if __name__ == "__main__":
    main()
