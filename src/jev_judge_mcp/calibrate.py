"""`jev-judge-mcp calibrate`: advisory threshold fitting on the caller's own labeled rows (ADR-0070).

Reads JSONL rows ``{"score", "correct", "family"?, "tool"?}`` — `score` is the scalar the tool
compares against its `auto_accept` threshold, `correct` is whether that judgment matched gold —
then splits the rows deterministically into a selection split and a held-out split (whole
families, never rows), picks the AUTO threshold with the most AUTO rows whose one-sided 95%
Clopper-Pearson upper error bound fits the error budget (`select_threshold`), and certifies that
point on the held-out rows (`certify`).

The command only reports. It never edits a frozen default in `jev_judge_mcp.policy.thresholds`:
moving one is a Sanctioned Divergence and needs an ADR. Exit 0 produced a report whose held-out
certification fits the budget, exit 1 means no threshold met the budget or the held-out bound
exceeded it, exit 2 is a bad invocation or bad rows.
"""

from __future__ import annotations

import json
import math
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from jev_judge_mcp.calibration.families import family_order
from jev_judge_mcp.calibration.targets import PRECISION_TARGETS, error_budget
from jev_judge_mcp.calibration.threshold import Certification, OperatingPoint, certify, select_threshold
from jev_judge_mcp.policy import PolicyThresholds, resolve_policy_thresholds

USAGE = "usage: jev-judge-mcp calibrate <rows.jsonl> [--tool TOOL] [--max-error P] [--min-rows N]\n"
ADVISORY = (
    "advisory only: this command reports a threshold for your traffic; it never changes the frozen"
    " defaults in policy/thresholds.py (moving a frozen default is a Sanctioned Divergence)"
)
DEFAULT_MIN_ROWS = 40
"""Below this the certified bound is noise; the guard refuses instead of guessing."""
MIN_HELD_OUT = 8
"""`certify` bounds at 1.0 with no evidence, so a held-out split smaller than this is refused."""
HOLDOUT_SHARE = 0.3
FIELDS = ("score", "correct", "family", "tool")
REVIEW_AT_TOOLS = ("jev_verify", "jev_review", "jev_gate")
"""The tools whose policy pairs `auto_accept` with `review_at`; the pairing itself is policy's own
`resolve_policy_thresholds`, not a copy of its rule."""
_SPLIT_SALT = "jev-judge-mcp-calibrate-v1"


class CalibrateError(Exception):
    """A bad invocation or bad rows; rendered to stderr with exit 2."""


@dataclass(frozen=True, slots=True)
class ParsedRow:
    score: float
    correct: bool
    family: str
    tool: str | None


def _reject_constant(value: str) -> None:
    raise ValueError(f"{value} is not a finite JSON number")


def _row(parsed: dict[str, Any], source: str, number: int) -> ParsedRow:
    where = f"{source}:{number}"
    unknown = sorted(set(parsed) - set(FIELDS))
    if unknown:
        raise CalibrateError(f"{where}: unknown field {unknown[0]!r}; need score, correct, optional family, tool")
    if "score" not in parsed or "correct" not in parsed:
        raise CalibrateError(f"{where}: need score and correct")
    score = parsed["score"]
    if isinstance(score, bool) or not isinstance(score, (int, float)) or not math.isfinite(score):
        raise CalibrateError(f"{where}: score must be a finite number")
    if not 0.0 <= score <= 1.0:
        raise CalibrateError(f"{where}: score {score} is outside [0, 1]")
    correct = parsed["correct"]
    if not isinstance(correct, bool):
        raise CalibrateError(f"{where}: correct must be true or false")
    family = parsed.get("family")
    if family is not None and (not isinstance(family, str) or not family):
        raise CalibrateError(f"{where}: family must be a non-empty string")
    tool = parsed.get("tool")
    if tool is not None and tool not in PRECISION_TARGETS:
        known = ", ".join(PRECISION_TARGETS)
        raise CalibrateError(f"{where}: unknown tool {tool!r} (one of {known})")
    return ParsedRow(float(score), correct, family if family else f"row{number}", tool)


def parse_rows(text: str, source: str) -> list[ParsedRow]:
    """Strict rows: every non-blank line must be one JSON object with the fields above."""
    rows = [
        _row(_decoded(line, source, number), source, number)
        for number, line in enumerate(text.splitlines(), 1)
        if line.strip()
    ]
    if not rows:
        raise CalibrateError(f"{source}: no rows")
    return rows


def _decoded(line: str, source: str, number: int) -> dict[str, Any]:
    where = f"{source}:{number}"
    try:
        parsed = json.loads(line, parse_constant=_reject_constant)
    except ValueError as error:
        raise CalibrateError(f"{where}: not one JSON object ({error})") from None
    if not isinstance(parsed, dict):
        raise CalibrateError(f"{where}: not one JSON object")
    return cast("dict[str, Any]", parsed)


def resolve_tool(flag_tool: str | None, rows: Sequence[ParsedRow]) -> str | None:
    """The one tool the rows calibrate, from `--tool` or the rows themselves; `None` when neither."""
    if flag_tool is not None and flag_tool not in PRECISION_TARGETS:
        known = ", ".join(PRECISION_TARGETS)
        raise CalibrateError(f"unknown tool {flag_tool!r} (one of {known})")
    if flag_tool is not None:
        other = next((row.tool for row in rows if row.tool is not None and row.tool != flag_tool), None)
        if other is not None:
            raise CalibrateError(f"a row carries tool {other!r}, not --tool {flag_tool!r}")
        return flag_tool
    row_tools = {row.tool for row in rows if row.tool is not None}
    if len(row_tools) > 1:
        named = ", ".join(sorted(row_tools))
        raise CalibrateError(f"rows carry the tools {named}; pass --tool to choose one")
    return next(iter(row_tools), None)


def split_rows(rows: Sequence[ParsedRow]) -> tuple[list[ParsedRow], list[ParsedRow]]:
    """`(selection, held_out)`: whole families to the held-out split until it reaches its share.

    Families are ordered by `sha256(salt + NUL + family)`, so the split is deterministic across
    processes and repeats. A family that would overflow the target stays in the selection split,
    so one big family never empties the selection; a row without a family is its own family.
    """
    families: dict[str, list[ParsedRow]] = {}
    for row in rows:
        families.setdefault(row.family, []).append(row)
    target = math.ceil(HOLDOUT_SHARE * len(rows))
    held_out: list[ParsedRow] = []
    selection: list[ParsedRow] = []
    for family in sorted(families, key=lambda name: family_order(name, _SPLIT_SALT)):
        group = families[family]
        if len(held_out) + len(group) <= target:
            held_out.extend(group)
        else:
            selection.extend(group)
    if len(held_out) < MIN_HELD_OUT:
        fittable = any(len(group) <= target for group in families.values())
        if fittable:
            raise CalibrateError(
                f"the held-out split has {len(held_out)} row(s), need at least {MIN_HELD_OUT}: add rows"
            )
        raise CalibrateError(
            f"the held-out split has {len(held_out)} row(s), need at least {MIN_HELD_OUT}: every"
            f" family is larger than the {HOLDOUT_SHARE:.0%} held-out share, so none can be held out whole"
        )
    return selection, held_out


def _render(
    name: str,
    tool: str | None,
    budget: float,
    rows: Sequence[ParsedRow],
    selection: Sequence[ParsedRow],
    held_out: Sequence[ParsedRow],
    point: OperatingPoint,
    certified: Certification,
) -> str:
    budget_line = (
        f"tool: {tool}; error budget: {budget:g}" if tool is not None else f"error budget: {budget:g} (--max-error)"
    )
    lines = [
        f"calibrate: {ADVISORY}.",
        f"rows file: {name}: {len(rows)} rows (selection {len(selection)}, held-out {len(held_out)};"
        " families kept whole)",
        f"{budget_line} (one-sided 95% Clopper-Pearson upper error bound)",
        f"recommended auto_accept: {point.threshold:g}",
        f"  selection: auto {point.auto}/{len(selection)}, errors {point.errors},"
        f" coverage {point.coverage:.3f}, upper error bound {point.error_upper_bound:.3f}",
        f"  held-out certification: auto {certified.auto}/{len(held_out)}, errors {certified.errors},"
        f" coverage {certified.coverage:.3f}, certified upper error bound {certified.error_upper_bound:.3f}",
    ]
    if tool in REVIEW_AT_TOOLS:
        resolved = resolve_policy_thresholds(point.threshold)
        assert isinstance(resolved, PolicyThresholds)
        lines.append(f"  pair with review_at: {resolved.review_at:g} (policy's own pairing of the pair)")
    verdict = (
        "the held-out bound is within the budget"
        if certified.error_upper_bound <= budget
        else "the held-out bound exceeds the budget: collect more rows before trusting this point"
    )
    lines.append(f"  {verdict}")
    return "\n".join(lines) + "\n"


def _options(argv: Sequence[str]) -> tuple[Path, str | None, float | None, int]:
    flags = {"--tool", "--max-error", "--min-rows"}
    positional: list[str] = []
    tool: str | None = None
    max_error: float | None = None
    min_rows: int | None = None
    index = 0
    while index < len(argv):
        arg = argv[index]
        if arg in flags:
            if index + 1 >= len(argv):
                raise CalibrateError(f"{arg} needs a value")
            value = argv[index + 1]
            if arg == "--tool":
                tool = value
            elif arg == "--max-error":
                try:
                    max_error = float(value)
                except ValueError:
                    raise CalibrateError(f"--max-error {value!r} is not a number") from None
                if not 0.0 < max_error < 1.0:
                    raise CalibrateError(f"--max-error {value} is outside (0, 1)")
            else:
                try:
                    min_rows = int(value)
                except ValueError:
                    raise CalibrateError(f"--min-rows {value!r} is not an integer") from None
                if min_rows < 2:
                    raise CalibrateError("--min-rows must be at least 2")
            index += 2
        elif arg.startswith("-"):
            raise CalibrateError(f"unknown option {arg!r}")
        else:
            positional.append(arg)
            index += 1
    if len(positional) != 1:
        raise CalibrateError("need exactly one rows file")
    return Path(positional[0]), tool, max_error, DEFAULT_MIN_ROWS if min_rows is None else min_rows


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if any(arg in ("-h", "--help") for arg in args):
        sys.stdout.write(f"{USAGE}\n{ADVISORY}.\n")
        return 0
    try:
        path, flag_tool, max_error, min_rows = _options(args)
        rows = parse_rows(path.read_text(encoding="utf-8"), path.name)
        if len(rows) < min_rows:
            raise CalibrateError(f"{len(rows)} rows, need at least {min_rows} (--min-rows)")
        tool = resolve_tool(flag_tool, rows)
        if max_error is not None:
            budget = max_error
        elif tool is not None:
            budget = error_budget(tool)
        else:
            raise CalibrateError("no error budget: pass --tool or --max-error")
        selection, held_out = split_rows(rows)
        point = select_threshold([(row.score, row.correct) for row in selection], max_error=budget)
        if point is None:
            zero_error_min = math.ceil(math.log(0.05) / math.log(1 - budget))
            sys.stderr.write(
                f"no threshold meets the error budget {budget:g} on the {len(selection)} selection rows;"
                f" a zero-error selection split needs at least {zero_error_min} rows at this budget"
                " — collect more rows or raise --max-error\n"
            )
            return 1
        certified = certify(point, [(row.score, row.correct) for row in held_out])
        sys.stdout.write(_render(path.name, tool, budget, rows, selection, held_out, point, certified))
        if certified.error_upper_bound > budget:
            sys.stderr.write(
                f"the held-out certification exceeds the error budget {budget:g}"
                f" ({certified.error_upper_bound:.3f} on {len(held_out)} rows); collect more rows\n"
            )
            return 1
        return 0
    except CalibrateError as error:
        sys.stderr.write(f"jev-judge-mcp calibrate: {error}\n{USAGE}")
        return 2
    except OSError as read_error:
        sys.stderr.write(f"jev-judge-mcp calibrate: could not read the rows file ({read_error})\n")
        return 2
