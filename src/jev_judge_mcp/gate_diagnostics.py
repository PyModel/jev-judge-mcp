"""Deterministic explanations for already decided gate actions (ADR-0081)."""

from collections.abc import Mapping, Sequence
from typing import Any

from jev_judge_mcp.limits import GATE
from jev_judge_mcp.text import length

type Blocker = dict[str, Any]

_RUBRIC_CHECKS = {
    "correctness": "Trace success and failure paths and run a regression for the changed behavior.",
    "spec_match": "Match each acceptance criterion to its changed hunk and an observable result.",
    "test_gap": "Identify the highest-risk changed branch and show the test that exercises it and its result.",
    "blast_radius": "Trace direct callers and shared interfaces and check their behavior with this change.",
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
    }


def input_cuts(
    args: Mapping[str, Any], evidence: Sequence[Mapping[str, object]], doc_units: int = GATE.doc_units
) -> list[Blocker]:
    """Recover each original document length before the gate's CapLedger discarded its suffix."""
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
                "next_check": f"Inspect the provider response for {field} and obtain a valid judgment.",
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
                "next_check": " ".join(
                    _RUBRIC_CHECKS[rubric] for rubric, row in levels.items() if row["level_assessment"] != "favorable"
                )
                or "Check the fractional rubric scores and weights against the requested composite floor.",
            }
        )
    return blockers


def _claim_blockers(verification: Mapping[str, Any], truncated: bool) -> list[Blocker]:
    blockers: list[Blocker] = []
    thresholds = verification["thresholds"]
    for row in verification["results"]:
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
                    "next_check": "Obtain a valid provider judgment for this claim.",
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
                }
            )
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
                    "next_check": "Check this exact claim against a direct observation.",
                }
            )
        elif verdict == "verified" and not truncated:
            blockers.append(
                {
                    **claim,
                    "field": "supporting_evidence",
                    "code": "caller_note_only",
                    "value": row.get("supporting_evidence") or "none",
                    "next_check": "Supply an independent observation for this claim.",
                }
            )
    return blockers


def explain_gate(body: dict[str, Any], blockers: list[Blocker], *, verification_cut: bool = False) -> None:
    """Append facts from every held file and claim while preserving legacy code and hint order."""
    if body["action"] == "auto":
        return
    if "verification" in body:
        blockers = [*blockers, *_claim_blockers(body["verification"], verification_cut)]
    body["blockers"] = blockers
    body["reason_codes"] = list(dict.fromkeys([*body["reason_codes"], *(b["code"] for b in blockers)]))
    body["next_checks"] = list(dict.fromkeys([*(b["next_check"] for b in blockers), *body["next_checks"]]))
