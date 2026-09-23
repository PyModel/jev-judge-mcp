"""The results view: one self-contained HTML file with inline SVG bar charts, no chart dependency.

Grouped bars compare arm A (direct, no Jev), arm B (automatic, Jev available), and arm C (forced)
over complete triplets only: accuracy (unmeasured until items are labeled), evaluator overhead
(items per minute), and time consumed (summed wall time, seconds). The page also states wall-time
spread, paired differences, Jev spend, Jev round-trip latency, and token totals. Colors are a
three-slot categorical set with light and dark steps. A `sample` view says so. The view only plots
records that exist.
"""

import html
import statistics
from collections.abc import Callable, Mapping, Sequence
from typing import Any, cast

from evals.bench import analysis
from evals.bench.stats import percentile

ARMS = (("A", "A direct"), ("B", "B automatic"), ("C", "C forced"))
WIDTH, BAR_H, GAP, LABEL_W, VALUE_W = 640, 14, 10, 150, 90
HISTORY = (
    "History: the 2026-09-22 two-arm Pi run recorded 150 pairs; after 43 with-Jev runs reached the "
    "local model, 127.0.0.1:8000 stopped accepting connections (Pi reported Connection error with zero "
    "tokens, on both arms) and the remaining 107 runs were server failures, not model speed."
)


def _groups(
    triplets: Mapping[str, analysis.Triplet],
) -> list[tuple[str, list[analysis.Triplet]]]:
    tools = sorted({str(triplet[0]["tool"]) for triplet in triplets.values()})
    grouped = [("All items", list(triplets.values()))]
    return grouped + [(tool, [t for t in triplets.values() if t[0]["tool"] == tool]) for tool in tools]


def _measures(runs: Sequence[analysis.Record]) -> dict[str, float]:
    wall = sum(float(run["wall_s"]) for run in runs)
    return {
        "accuracy": sum(bool(run["correct"]) for run in runs) / len(runs),
        "speed": len(runs) / (wall / 60) if wall > 0 else 0.0,
        "time": wall,
    }


Format = Callable[[float], str]
CHARTS: tuple[tuple[str, str, str, Format], ...] = (
    ("accuracy", "Accuracy: share of items correct", "share correct", lambda v: f"{v * 100:.1f}%"),
    ("speed", "Evaluator overhead: items per minute (items / summed wall time)", "items/min", lambda v: f"{v:.2f}/min"),
    ("time", "Time consumed: total wall time", "seconds", lambda v: f"{v:.2f} s"),
)


def _ticks(scale: float) -> list[float]:
    return [scale * step / 4 for step in range(5)]


def _chart(key: str, title: str, unit: str, fmt: Format, rows: list[tuple[str, dict[str, dict[str, float]]]]) -> str:
    """Horizontal grouped bars on one baseline: thin bars with a 2px gap, 4px rounded data ends,
    recessive quarter gridlines, a value label at each bar end, and a hover title per bar."""
    top = max((m[arm][key] for _, m in rows for arm, _ in ARMS), default=0.0)
    scale = 1.0 if key == "accuracy" else (top or 1.0)
    plot = WIDTH - LABEL_W - VALUE_W
    group_h = len(ARMS) * BAR_H + GAP
    body_h = len(rows) * group_h
    height = body_h + 36
    parts = [f'<svg viewBox="0 0 {WIDTH} {height}" role="img" aria-label="{html.escape(title)}">']
    for tick in _ticks(scale):
        x = LABEL_W + plot * tick / scale
        parts.append(f'<line class="grid" x1="{x:.1f}" x2="{x:.1f}" y1="6" y2="{body_h + 8}"/>')
        parts.append(
            f'<text class="tick" x="{x:.1f}" y="{body_h + 22}" text-anchor="middle">{html.escape(fmt(tick))}</text>'
        )
    for index, (label, measures) in enumerate(rows):
        y = 8 + index * group_h
        parts.append(
            f'<text class="lbl" x="{LABEL_W - 10}" y="{y + BAR_H + 4}" text-anchor="end">{html.escape(label)}</text>'
        )
        for offset, (arm, name) in enumerate(ARMS):
            value = measures[arm][key]
            width = max(0.0, plot * value / scale)
            by = y + offset * BAR_H
            tip = html.escape(f"{label} · {name}: {fmt(value)}")
            parts.append(
                f'<rect class="arm{arm}" x="{LABEL_W}" y="{by}" width="{width:.1f}" height="{BAR_H - 2}" rx="4">'
                f"<title>{tip}</title></rect>"
            )
            parts.append(
                f'<text class="val" x="{LABEL_W + width + 6:.1f}" y="{by + BAR_H - 4}">{html.escape(fmt(value))}</text>'
            )
    parts.append(f'<line class="axis" x1="{LABEL_W}" x2="{LABEL_W}" y1="4" y2="{body_h + 8}"/>')
    parts.append(
        f'<text class="unit" x="{WIDTH - 4}" y="{height - 2}" text-anchor="end">unit: {html.escape(unit)}</text></svg>'
    )
    return f"<figure><figcaption>{html.escape(title)}</figcaption>{''.join(parts)}</figure>"


def _legend() -> str:
    keys = "".join(f'<span><i class="sw arm{arm}"></i>{html.escape(name)}</span>' for arm, name in ARMS)
    return f'<div class="legend">{keys}</div>'


def _cell(value: object) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:.4g}"
    return str(value)


def _headline(triplets: Mapping[str, analysis.Triplet], unmeasured: str | None = None) -> str:
    result: dict[str, object] = {}
    if unmeasured is not None:
        result["accuracy"] = unmeasured
    result.update(analysis.headline(triplets))
    rows = "".join(f"<tr><th>{html.escape(k)}</th><td>{html.escape(_cell(v))}</td></tr>" for k, v in result.items())
    return f"<table>{rows}</table>"


def _gate_cell(record: analysis.Record) -> str:
    if record.get("server_failure"):
        return "server failure (not an arm result)"
    if record["arm"] == "A":
        return "n/a (arm has no Jev)"
    if record.get("gate") == "did not call Jev":
        return "did not call Jev"
    if record.get("gate"):
        return str(record["gate"])
    return "used Jev"


def _runs(records: Sequence[analysis.Record]) -> str:
    columns = ("run", "tool", "status", "use gate", "answer", "correct", "wall s", "Jev calls")
    head = "<tr>" + "".join(f"<th>{column}</th>" for column in columns) + "</tr>"
    body = "".join(
        "<tr>"
        + "".join(
            f"<td>{html.escape(str(v))}</td>"
            for v in (
                r["run_id"],
                r["tool"],
                r["status"],
                _gate_cell(r),
                r["answer"] if r["answer"] is not None else r["parse_error"],
                r["correct"],
                f"{float(r['wall_s']):.2f}",
                len(r["jev_calls"]),
            )
        )
        + "</tr>"
        for r in sorted(records, key=lambda r: str(r["run_id"]))
    )
    return f"<table>{head}{body}</table>"


_LIGHT = (
    "color-scheme:light;--bg:#f6f6f4;--card:#fcfcfb;--fg:#0b0b0b;--muted:#52514e;--faint:#8a8984;"
    "--line:#e6e5e0;--grid:#eeede8;--a:#2a78d6;--b:#eb6834;--c:#0f7a5a;--accent:#2a78d6"
)
_DARK = (
    "color-scheme:dark;--bg:#111110;--card:#1a1a19;--fg:#ffffff;--muted:#c3c2b7;--faint:#8f8e86;"
    "--line:#2e2e2b;--grid:#262624;--a:#3987e5;--b:#d95926;--c:#3dba6c;--accent:#3987e5"
)
STYLE = (
    f":root{{{_LIGHT}}}"
    f"@media (prefers-color-scheme:dark){{:root:not([data-theme=light]){{{_DARK}}}}}"
    f":root[data-theme=dark]{{{_DARK}}}"
    """
*{box-sizing:border-box}
body{background:var(--bg);color:var(--fg);margin:0 auto;max-width:760px;padding:24px 16px 48px;
font:14px/1.5 ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif;-webkit-font-smoothing:antialiased}
h1{font-size:22px;font-weight:650;letter-spacing:-.01em;margin:0 0 12px}
h2{font-size:15px;font-weight:600;margin:32px 0 8px;color:var(--fg)}
p{color:var(--muted);margin:6px 0}
.banner{background:var(--card);border:1px solid var(--line);border-left:3px solid var(--accent);
border-radius:8px;padding:10px 14px;margin:12px 0;color:var(--fg)}
figure{background:var(--card);border:1px solid var(--line);border-radius:12px;margin:16px 0;padding:16px 16px 10px}
figcaption{font-weight:600;margin-bottom:10px}
svg{width:100%;height:auto;display:block;font-variant-numeric:tabular-nums}
.armA{fill:var(--a);background:var(--a)}.armB{fill:var(--b);background:var(--b)}.armC{fill:var(--c);background:var(--c)}
rect.armA:hover,rect.armB:hover,rect.armC:hover{opacity:.82}
.grid{stroke:var(--grid);stroke-width:1}.axis{stroke:var(--line);stroke-width:1}
.lbl{fill:var(--fg);font-size:12px}.val{fill:var(--muted);font-size:11.5px}
.tick,.unit{fill:var(--faint);font-size:11px}
.legend{display:flex;flex-wrap:wrap;gap:18px;margin:16px 0 4px;color:var(--muted);font-size:13px}
.sw{display:inline-block;width:10px;height:10px;border-radius:3px;margin-right:7px;vertical-align:0}
table{border-collapse:collapse;width:100%;margin:8px 0;font-size:13px;display:block;overflow-x:auto;
font-variant-numeric:tabular-nums}
th,td{border-bottom:1px solid var(--line);padding:6px 10px;text-align:left;white-space:nowrap}
th{color:var(--muted);font-weight:500}
"""
)


def _unmeasured(title: str, note: str) -> str:
    return f"<figure><figcaption>{html.escape(title)}</figcaption><p>{html.escape(note)}</p></figure>"


GRADING_FAILURE = "failed: post-processing raised"


def _reached(run: analysis.Record) -> bool:
    return run["status"] == "ok"


def _grading_failed(run: analysis.Record) -> bool:
    """The run reached the model, then grading crashed: neither a connection error nor a Jev miss."""
    return run["status"].startswith(GRADING_FAILURE)


def _measured_triplets(triplets: Mapping[str, analysis.Triplet]) -> dict[str, analysis.Triplet]:
    """Triplets where every arm reached the model. A connection error is not model speed."""
    return {item: triplet for item, triplet in triplets.items() if all(_reached(run) for run in triplet)}


def _connection(records: Sequence[analysis.Record]) -> tuple[int, int, int, int]:
    """Answered, model-reached, unreachable, and grading-failed B and C runs.

    Server failures are not arm results and are left out. Answered means `gate is None`.
    """
    jev = [run for run in records if run["arm"] in ("B", "C") and not run.get("server_failure")]
    reached = [run for run in jev if _reached(run)]
    graded = sum(_grading_failed(run) for run in jev)
    return sum(run.get("gate") is None for run in reached), len(reached), len(jev) - len(reached) - graded, graded


def _connection_line(records: Sequence[analysis.Record]) -> str:
    answered, reached, unreachable, graded = _connection(records)
    share = f"{answered / reached * 100:.0f}%" if reached else "n/a"
    line = f"Jev connection: {answered} of {reached} with-Jev runs that reached the model got a Jev answer ({share})."
    if unreachable:
        line += f" {unreachable} with-Jev runs never reached the local model (connection error) and are not a Jev miss."
    if graded:
        line += f" {graded} with-Jev runs failed in grading (post-processing raised) and are not a Jev miss."
    automatic = [run for run in records if run["arm"] == "B" and _reached(run) and not run.get("server_failure")]
    skipped = sum(run.get("gate") == "did not call Jev" for run in automatic)
    if automatic:
        called = len(automatic) - skipped
        line += (
            f" Automatic arm: {called} of {len(automatic)} model-reached B runs called Jev; {skipped} did not call Jev."
        )
    return line


def _dropped_note(
    triplets: Mapping[str, analysis.Triplet],
    measured: Mapping[str, analysis.Triplet],
) -> str:
    """Why unmeasured triplets dropped out: a grading failure in any arm, else a connection error."""
    dropped = [triplet for item, triplet in triplets.items() if item not in measured]
    graded = sum(any(_grading_failed(run) for run in triplet) for triplet in dropped)
    unreachable = len(dropped) - graded
    parts: list[str] = []
    if unreachable:
        parts.append(f"{unreachable} triplets failed before an answer (local-server connection error)")
    if graded:
        parts.append(f"{graded} triplets failed in grading (post-processing raised)")
    return "; ".join(parts) + (" and are not model speed." if len(parts) == 1 else ". Neither is model speed.")


def _usage(record: analysis.Record) -> dict[str, Any]:
    raw = record.get("usage")
    return cast(dict[str, Any], raw) if isinstance(raw, dict) else {}


def _round_trips(records: Sequence[analysis.Record]) -> list[float]:
    samples: list[float] = []
    for record in records:
        if record.get("server_failure"):
            continue
        timings = record.get("timings")
        if not isinstance(timings, list):
            continue
        for timing in cast(list[object], timings):
            if not isinstance(timing, dict):
                continue
            milliseconds = cast(dict[str, Any], timing).get("round_trip_ms")
            if isinstance(milliseconds, int | float):
                samples.append(float(milliseconds))
    return samples


def _latency_line(records: Sequence[analysis.Record]) -> str:
    samples = _round_trips(records)
    if not samples:
        return "Jev round trip: not measured (no timed calls)."
    return (
        f"Jev round trip: n={len(samples)}, median {statistics.median(samples):.1f} ms, "
        f"p90 {percentile(samples, 90):.1f} ms, p95 {percentile(samples, 95):.1f} ms."
    )


def _token_line(records: Sequence[analysis.Record]) -> str:
    """Pi's JSON usage fields, summed per arm. Missing usage is not estimated."""
    reported = [record for record in records if not record.get("server_failure") and _usage(record).get("reported")]
    if not reported:
        return "Tokens: not measured."
    parts: list[str] = []
    for arm, _name in ARMS:
        rows = [record for record in reported if record["arm"] == arm]
        if not rows:
            parts.append(f"{arm} not measured")
            continue
        totals = {
            key: sum(int(_usage(record).get(key) or 0) for record in rows)
            for key in ("input", "output", "cache_read", "cache_write")
        }
        parts.append(
            f"{arm} input {totals['input']} / output {totals['output']} / "
            f"cache read {totals['cache_read']} / cache write {totals['cache_write']}"
        )
    return "Tokens (Pi usage fields, summed): " + "; ".join(parts) + "."


def _agent_spend_line(records: Sequence[analysis.Record]) -> str:
    """Pi's usage cost, summed per arm. A missing cost object is not estimated as zero."""
    reported = [record for record in records if not record.get("server_failure") and record.get("agent_cost_reported")]
    if not reported:
        return "Agent model spend: not measured."
    parts: list[str] = []
    for arm, _name in ARMS:
        rows = [record for record in reported if record["arm"] == arm]
        if not rows:
            parts.append(f"{arm} not measured")
            continue
        total = sum(float(record.get("agent_cost_usd") or 0) for record in rows)
        parts.append(f"{arm} ${total:.4f}")
    return "Agent model spend (Pi usage cost, summed): " + "; ".join(parts) + "."


def render(
    records: Sequence[analysis.Record],
    *,
    sample: bool,
    stop: str,
    labeled_items: int | None = None,
    model_id: str | None = None,
    thinking: str | None = None,
) -> str:
    triplets = analysis.complete_triplets(records)
    measured = _measured_triplets(triplets)
    dropped = len(triplets) - len(measured)
    title = "Jev bench results" + (" (SAMPLE: dry-run stub data)" if sample else "")
    if sample:
        banner = (
            "SAMPLE: these numbers come from the offline dry run's stub agent and loopback provider. "
            "They are not a bench result."
        )
    elif dropped:
        banner = (
            f"{len(triplets)} triplets recorded. Speed and time use the {len(measured)} triplets where every arm "
            f"reached the model. {_dropped_note(triplets, measured)}"
        )
    else:
        banner = f"{len(triplets)} complete triplets."
    # No gold labels means accuracy is unknowable: say so instead of plotting a bar that would read as 0%.
    unmeasured = f"not measured ({labeled_items} labeled items)" if labeled_items == 0 else None
    plot = measured if dropped else triplets
    if plot:
        rows = [
            (label, {arm: _measures([triplet[i] for triplet in group]) for i, (arm, _) in enumerate(ARMS)})
            for label, group in _groups(plot)
        ]
        charts = "".join(
            _unmeasured(t, unmeasured) if key == "accuracy" and unmeasured else _chart(key, t, unit, fmt, rows)
            for key, t, unit, fmt in CHARTS
        )
    else:
        charts = "<p>No complete triplets recorded: nothing to plot.</p>"
    model = f"Model: {model_id}." if model_id else "Model: not recorded."
    effort = f"Thinking: {thinking}." if thinking else "Thinking: not recorded."
    return (
        f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
        f'<meta name="viewport" content="width=device-width,initial-scale=1"><title>{html.escape(title)}</title>'
        f"<style>{STYLE}</style></head><body><h1>{html.escape(title)}</h1>"
        f'<p class="banner">{html.escape(banner)}</p><p>{html.escape(model)} {html.escape(effort)}</p>'
        f"<p>{html.escape(_connection_line(records))}</p><p>{html.escape(_latency_line(records))}</p>"
        f"<p>{html.escape(_token_line(records))}</p><p>{html.escape(_agent_spend_line(records))}</p>"
        f"<p>{html.escape(HISTORY)}</p>"
        f"<p>Stop: {html.escape(stop)}</p>{_legend()}{charts}"
        f"<h2>Headline (complete triplets)</h2>{_headline(plot, unmeasured=unmeasured)}"
        f"<h2>Runs</h2>{_runs(records)}</body></html>\n"
    )


def _fmt(value: object) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value)


def numbers_md(
    records: Sequence[analysis.Record],
    *,
    labeled_items: int,
    stop: str,
    model_id: str | None = None,
    thinking: str | None = None,
) -> str:
    """The numbers table that cites the run records. Accuracy is unmeasured when nothing is labeled.

    Jev spend and agent model spend are separate. Wall time is the median, p90, and p95 per arm,
    and the paired differences B minus A and C minus A, over triplets where every arm reached the model.
    """
    triplets = analysis.complete_triplets(records)
    measured = _measured_triplets(triplets)
    summary = analysis.headline(measured)
    accuracy = f"not measured ({labeled_items} labeled items)"
    answered, reached, unreachable, graded = _connection(records)
    share = f"{answered / reached * 100:.0f}%" if reached else "n/a"
    automatic = [run for run in records if run["arm"] == "B" and _reached(run) and not run.get("server_failure")]
    skipped = sum(run.get("gate") == "did not call Jev" for run in automatic)
    forced = [run for run in records if run["arm"] == "C" and _reached(run) and not run.get("server_failure")]
    forced_answered = sum(run.get("gate") is None for run in forced)
    note = f"\n\n{unreachable} with-Jev runs never reached the local model (connection error)." if unreachable else ""
    if graded:
        note += f"\n\n{graded} with-Jev runs failed in grading (post-processing raised)."
    model = model_id or "not recorded"
    effort = thinking or "not recorded"

    def row(arm: str, label: str) -> str:
        jev = float(summary.get(f"jev_usd_{arm}") or 0)
        return (
            f"| {label} | {summary['triplets']} | {_fmt(summary.get(f'wall_{arm}_median_s'))} | "
            f"{_fmt(summary.get(f'wall_{arm}_p90_s'))} | {_fmt(summary.get(f'wall_{arm}_p95_s'))} | "
            f"{jev:.4f} | {summary.get(f'called_jev_{arm}')} |"
        )

    def paired(label: str, key: str) -> str:
        return (
            f"| {label} | {_fmt(summary.get(f'wall_{key}_median_s'))} | "
            f"{_fmt(summary.get(f'wall_{key}_p90_s'))} | {_fmt(summary.get(f'wall_{key}_p95_s'))} |"
        )

    return (
        "# Jev bench: direct, automatic, and forced\n\n"
        f"Accuracy: {accuracy}.\n\n"
        f"Model: {model}.\n\n"
        f"Thinking: {effort}.\n\n"
        "Jev spend is capped at 25 USD. Agent model spend is separate and is not measured when Pi "
        "reports no usage cost.\n\n"
        "| arm | triplets | median wall s | p90 | p95 | Jev spend USD | called Jev |\n"
        "|---|---|---|---|---|---|---|\n"
        f"{row('A', 'A direct')}\n"
        f"{row('B', 'B automatic')}\n"
        f"{row('C', 'C forced')}\n\n"
        "| paired difference | median s | p90 | p95 |\n"
        "|---|---|---|---|\n"
        f"{paired('B minus A', 'b_minus_a')}\n"
        f"{paired('C minus A', 'c_minus_a')}\n\n"
        f"Automatic arm: {len(automatic) - skipped} of {len(automatic)} B runs called Jev; "
        f"{skipped} did not call Jev.\n\n"
        f"Forced arm: {forced_answered} of {len(forced)} model-reached C runs got a Jev answer.\n\n"
        f"Jev connection (model-reached B and C runs with a Jev answer): {answered}/{reached} ({share}).\n\n"
        f"{_latency_line(records)}\n\n"
        f"{_token_line(records)}\n\n"
        f"{_agent_spend_line(records)}\n\n"
        f"Stop: {stop}{note}\n\n"
        f"{HISTORY}\n\n"
        "Speed and time use triplets where every arm reached the model. "
        "Source: run records `evals/reports/bench150/*/result.json` (raw transcripts gitignored).\n"
    )
