"""Deterministic explanations for already decided gate actions (ADR-0081)."""

from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any, Literal

from jev_judge_mcp.limits import GATE
from jev_judge_mcp.responses import EXCERPT_CHECK, NO_HUNKS_CHECK, diff_shape
from jev_judge_mcp.text import length

type Blocker = dict[str, Any]

type NextCall = Literal["none", "with_input"]
"""Who settles a blocker (ADR-0089). `with_input`: one more Jev call that adds the input its next_check
names. `none`: the caller settles it with its own check; an unchanged re-call cannot move it."""

_RUBRIC_CHECKS = {
    "correctness": "Run one regression that asserts the changed behavior.",
    "spec_match": "Verify the requested behavior against the changed hunk.",
    "test_gap": "Show the result of a test that exercises the highest-risk changed branch.",
    "blast_radius": "Check a direct caller against the changed interface.",
}


def size_blocker(field: str, units: int, limit: int) -> Blocker:
    return {
        "code": field.split("[")[0] + "_over_budget",
        "field": field,
        "value": units,
        "threshold": limit,
        "comparison": ">",
        "unit": "utf16",
        "next_check": f"Review omitted {field} input with its supporting evidence in a bounded change.",
        "next_call": "with_input",
    }


def input_cuts(
    args: Mapping[str, Any], evidence: Sequence[Mapping[str, object]], doc_units: int = GATE.doc_units
) -> list[Blocker]:
    """Identify caller documents over their UTF-16 limits."""
    docs = [(k, args[k], doc_units) for k in ("request", "diff", "tests") if isinstance(args.get(k), str)]
    docs.extend((f"claims[{i}]", v, GATE.claim_units) for i, v in enumerate(args.get("claims", [])))
    docs.extend((f"evidence[{i}]", str(item["text"]), doc_units) for i, item in enumerate(evidence))
    return [size_blocker(k, n, cap) for k, v, cap in docs if (n := length(v)) > cap]


def _level(rubric: str, row: Mapping[str, Any]) -> Blocker:
    level = row["level"]
    favorable = 0 if rubric in ("test_gap", "blast_radius") else 2
    assessment = "intermediate" if level == 1 else "favorable" if level == favorable else "adverse"
    return {"score": row["score"], "level": level, "level_assessment": assessment}


def review_blockers(review: Mapping[str, Any]) -> list[Blocker]:
    if review["action"] == "auto":
        return []
    scores = review["scores"]
    if review.get("status") == "invalid_response":
        missing = [rubric for rubric in scores if scores[rubric]["score"] is None]
        if review["safe_to_apply"] is None:
            missing.append("safe_to_apply")
        return [
            {
                "field": field,
                "code": f"{field}_invalid",
                "value": None,
                "next_check": f"No valid {field} judgment came back. Check {field} yourself.",
                "next_call": "none",
            }
            for field in missing
        ]
    name = "review_at" if review["action"] == "escalate" else "auto_accept"
    threshold = review["thresholds"][name]
    blockers: list[Blocker] = []
    if (safe := review["safe_to_apply"]) < threshold:
        blockers.append(
            {
                "field": "safe_to_apply",
                "code": f"safe_to_apply_below_{name}",
                "value": safe,
                "comparison": "<",
                "threshold_name": name,
                "threshold": threshold,
                "next_check": "Inspect the change's failure and recovery paths before applying it.",
                "next_call": "none",
            }
        )
    for rubric, check in _RUBRIC_CHECKS.items():
        row = scores[rubric]
        confidence = row["confidence"]
        if confidence is None or confidence < threshold:
            blockers.append(
                {
                    "field": f"{rubric}.confidence",
                    "code": f"{rubric}_confidence_unknown"
                    if confidence is None
                    else f"{rubric}_confidence_below_{name}",
                    "value": confidence,
                    "comparison": "unknown" if confidence is None else "<",
                    "threshold_name": name,
                    "threshold": threshold,
                    **_level(rubric, row),
                    "next_check": check,
                    "next_call": "none",
                }
            )
    if name == "auto_accept" and review["composite"] < review["thresholds"]["composite_floor"]:
        levels = {rubric: _level(rubric, row) for rubric, row in scores.items()}
        blockers.append(
            {
                "field": "composite",
                "code": "composite_below_floor",
                "value": review["composite"],
                "comparison": "<",
                "threshold_name": "composite_floor",
                "threshold": review["thresholds"]["composite_floor"],
                "rubrics": levels,
                "next_check": next(
                    (_RUBRIC_CHECKS[key] for key, row in levels.items() if row["level_assessment"] != "favorable"),
                    "Check the fractional rubric scores against the requested composite floor.",
                ),
                "next_call": "none",
            }
        )
    return blockers


def _claim_blockers(verification: Mapping[str, Any], source_issues: Mapping[int, str]) -> list[Blocker]:
    blockers: list[Blocker] = []
    thresholds = verification["thresholds"]
    for index, row in enumerate(verification["results"]):
        if row["action"] == "auto":
            continue
        claim = {"claim_id": row["id"]}
        verdict, confidence = row["verdict"], row["confidence"]
        if verdict is None:
            blockers.append(
                {
                    **claim,
                    "field": "verdict",
                    "code": "claim_invalid",
                    "value": None,
                    "invalid_answer": f"claim_{index}",
                    "next_check": "No valid judgment came back for this claim. Check it yourself against a direct "
                    "observation (a test, command output, or the source).",
                    "next_call": "none",
                }
            )
            continue
        if verdict != "verified":
            blockers.append(
                {
                    **claim,
                    "field": "verdict",
                    "code": f"claim_{verdict}",
                    "value": verdict,
                    "confidence": confidence,
                    "auto_accept": thresholds["auto_accept"],
                    "next_check": "Reconcile the claim with its cited evidence and check the disputed behavior."
                    if verdict == "contradicted"
                    else "Supply direct evidence for this claim or narrow its wording.",
                    "next_call": "none" if verdict == "contradicted" else "with_input",
                }
            )
            if row.get("reported_verdict") == "contradicted" and verdict == "unsupported":
                blockers[-1].update(
                    code="claim_contradiction_unconfirmed",
                    next_check="Supply the exact observation that contradicts this claim.",
                )
            elif verdict == "contradicted":
                line = row["evidence_line"]
                blockers[-1].update(
                    evidence_line=line,
                    next_check=f"Reconcile the claim with {line['source']} line {line['line']} ({line['text']}).",
                )
            elif row.get("missing_evidence"):
                missing = {
                    "needs_before_after": "before and after observations",
                    "needs_tests": "test output",
                    "needs_diff": "the changed hunk",
                    "single_item_no_source": "an independent observation",
                }
                blockers[-1]["next_check"] = f"Supply {missing[row['missing_evidence']]} for this claim."
            if verdict == "contradicted" and confidence is not None and confidence >= thresholds["auto_accept"]:
                blockers[-1]["confidence_comparison"] = ">="
        if confidence is None or confidence < thresholds["auto_accept"]:
            name = "review_at" if confidence is None or confidence < thresholds["review_at"] else "auto_accept"
            blockers.append(
                {
                    **claim,
                    "field": "claim.confidence",
                    "code": "claim_confidence_unknown" if confidence is None else f"claim_confidence_below_{name}",
                    "value": confidence,
                    "comparison": "unknown" if confidence is None else "<",
                    "threshold_name": name,
                    "threshold": thresholds[name],
                    "next_check": "Settle this claim yourself against a direct observation (a test, command output, "
                    "or the source).",
                    "next_call": "none",
                }
            )
        elif (issue := source_issues.get(index)) is not None:
            blockers.append(
                {
                    **claim,
                    "field": "supporting_evidence",
                    "code": issue,
                    "value": row.get("supporting_evidence") or "none",
                    "next_check": "Supply an independent observation for this claim."
                    if issue != "source_invalid"
                    else "No valid source judgment came back. Check yourself which evidence supports this claim.",
                    "next_call": "none" if issue == "source_invalid" else "with_input",
                    **({"invalid_answer": f"source_{index}"} if issue == "source_invalid" else {}),
                }
            )
    return blockers


def explain_gate(body: dict[str, Any], blockers: list[Blocker], diff: object, source_issues: Mapping[int, str]) -> None:
    """One change verdict, with a count per diagnostic, one highest-priority check, and who settles it.

    The action is decided before this runs and is never changed here (ADR-0089).
    """
    claim_blockers = _claim_blockers(body["verification"], source_issues)
    for blocker in claim_blockers:
        blocker["next_check"] = f"{blocker['claim_id']}: {blocker['next_check']}"
    blockers = [*blockers, *claim_blockers]
    counts = Counter(str(b["code"]) for b in blockers)
    body["rollup"] = {"action": body["action"], "reason_counts": dict(counts)}
    shape_check = {"text": NO_HUNKS_CHECK, "excerpt": EXCERPT_CHECK}.get(diff_shape(diff))
    if not blockers:
        body["next_checks"] = (
            ["The row stands. Proceed."] if body["action"] == "auto" else ([shape_check] if shape_check else [])
        )
        body["rollup"]["next_call"] = _next_call(blockers, body["next_checks"], shape_check)
        return
    body["blockers"] = blockers
    priority = next((b for b in blockers if "evidence_line" in b), None)
    if priority is None:
        priority = next((b for b in blockers if b["code"] == "claim_invalid"), None)
    if priority is None:
        priority = next(
            (
                b
                for b in blockers
                if b["code"] in ("file_withheld", "context_omitted", "claim_contradiction_unconfirmed")
            ),
            None,
        )
    if priority is not None:
        check = priority["next_check"]
    elif shape_check:
        check = shape_check
    else:
        check = (claim_blockers or blockers)[0]["next_check"]
    body["next_checks"] = [check]
    body["rollup"]["next_call"] = _next_call(blockers, body["next_checks"], shape_check)


def _next_call(blockers: list[Blocker], next_checks: list[str], shape_check: str | None) -> NextCall:
    """`with_input` when some blocker, or the emitted re-pack check, names input a new call can carry."""
    repack = shape_check is not None and shape_check in next_checks
    return "with_input" if repack or any(b["next_call"] == "with_input" for b in blockers) else "none"
