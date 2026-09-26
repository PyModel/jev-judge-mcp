#!/usr/bin/env python3
"""Risk-proportional thresholds: the caller raises the bar, not the tool.

jev-mcp's thresholds are uniform. A judgment clears `auto` at `auto_accept` (default 0.8 for
jev_verify, jev_review, jev_gate; 0.85 for jev_classify, jev_compare, jev_extract) whatever the
next step is. The tool does not know that one `auto` precedes a reversible edit and another
precedes `rm -rf`. This example is the caller-side half of ADR-0002 (the model judges, policy
decides): read one decision result, and demand a stricter bar before a destructive step than a
reversible one.

Run it against a decision result from the CLI judge (docs/adr/0064-cli-judge-and-gate.md):

    jev-judge-mcp judge jev_review < review-args.json > decision.json
    python examples/risk_proportional_thresholds.py decision.json --stakes reversible
    python examples/risk_proportional_thresholds.py decision.json --stakes destructive

Exit codes: 0 proceed, 1 stop (honor the tool's action), 2 confirm with a human first.
The thresholds below are this example's, not the server's; set your own from your traffic.
See docs/guidance.md ("Threshold on what the answer actually gives you") and docs/EVIDENCE.md
for what is certified so far.
"""

import argparse
import json
import sys
from typing import cast

REVERSIBLE_BAR = 0.8
"""For a reversible step, the tool's own bar stands: its `auto` is enough."""

IRREVERSIBLE_BAR = 0.95
"""Before a destructive or irreversible step, every confidence must clear this too."""

_DESCRIPTION = "Risk-proportional thresholds: the caller raises the bar, not the tool."

CONFIDENCE_KEYS = ("confidence", "safe_to_apply")
"""Fields that assert a probability or concentration. The binding constraint is the minimum, so
a low-confidence ancillary row (a test_gap score on jev_review, one weak claim on jev_gate's
verification) can hold back a destructive step on its own."""


def _collect(node: object, found: list[float]) -> None:
    """Walk the payload recursively: every numeric `confidence` and `safe_to_apply` counts.

    The real payloads differ per tool — jev_verify's flat `results[]`, jev_review's
    `scores{rubric}` plus top-level `safe_to_apply`, jev_gate's nested `review.scores` and
    `verification.results[]` — so the walk follows the shape instead of naming paths. A boolean
    is not a confidence even though Python's bool subclasses int.
    """
    if isinstance(node, dict):
        for key, value in cast(dict[str, object], node).items():
            if key in CONFIDENCE_KEYS and isinstance(value, (int, float)) and not isinstance(value, bool):
                found.append(float(value))
            else:
                _collect(value, found)
    elif isinstance(node, list):
        for item in cast(list[object], node):
            _collect(item, found)


def confidences(envelope: dict[str, object]) -> list[float]:
    """Every confidence the decision result asserts, anywhere in it: the envelope's own, rubric
    scores, safe_to_apply, verification rows, per-item rows."""
    found: list[float] = []
    _collect(envelope, found)
    return found


def gate(envelope: dict[str, object], stakes: str) -> tuple[int, str]:
    """One decision result, one next step: proceed, confirm, or stop."""
    if envelope.get("unresolved") or envelope.get("error") is not None:
        return 1, "stop: the decision is unresolved; treat it as unjudged"
    action = envelope.get("action")
    if action != "auto":
        return 1, f"stop: honor the action '{action}' before doing anything else"
    if stakes == "reversible":
        return 0, "proceed: the tool's auto is the bar for a reversible step"
    values = confidences(envelope)
    if not values:
        return 2, "confirm: no confidence to raise the bar on; a human checks before the destructive step"
    weakest = min(values)
    if weakest >= IRREVERSIBLE_BAR:
        return 0, f"proceed: every confidence ({weakest:.4f} at the weakest) clears {IRREVERSIBLE_BAR}"
    return 2, (
        f"confirm: weakest confidence {weakest:.4f} is under {IRREVERSIBLE_BAR}; "
        "a destructive step needs a human or a stronger check first"
    )


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=_DESCRIPTION)
    parser.add_argument("decision", help="a DecisionResult JSON file from `jev-judge-mcp judge <tool>`")
    parser.add_argument(
        "--stakes",
        choices=("reversible", "destructive"),
        default="reversible",
        help="what runs next; destructive steps face the stricter bar",
    )
    args = parser.parse_args(argv)
    with open(args.decision, encoding="utf-8") as handle:
        loaded: object = json.load(handle)
    if not isinstance(loaded, dict):
        print("stop: the decision file is not one JSON object", file=sys.stderr)
        return 1
    code, message = gate(cast(dict[str, object], loaded), args.stakes)
    print(message)
    return code


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
