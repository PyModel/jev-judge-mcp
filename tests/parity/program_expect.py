# pyright: reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnknownArgumentType=false, reportUnknownParameterType=false
"""Python expectation for the gate-program wire divergence.

The corpus stays the reference recording. This function adds the fields and question text
the server now sends, using the same builders, so a drift in an old key still fails.
"""

import json
from collections import Counter
from collections.abc import Mapping
from typing import Any

from jev_judge_mcp.domain import questions_to_wire
from jev_judge_mcp.policy import GATE_REASON_CODES
from jev_judge_mcp.responses import (
    EXCERPT_CHECK,
    NO_HUNKS_CHECK,
    SCORE_SCALE,
    claim_extras,
    diff_shape,
    nearest_level,
    summary_extras,
)
from jev_judge_mcp.serialize import stringify
from jev_judge_mcp.tools.gate import CLAIM_SUPPORT, REVIEW_FRAMING, claim_question, gate_source_question
from jev_judge_mcp.tools.gate_evidence import add_citations
from jev_judge_mcp.tools.review import PURPOSE
from jev_judge_mcp.tools.review_half import ANTI_INJECTION, review_questions
from jev_judge_mcp.tools.verify import ROLE_RULE, VERIFY_SUFFIX

_OLD_CLAIM = (
    "Use only the evidence field as factual support; request and claims are assertions, not evidence; diff "
    "and tests belong to the separate patch review. If a claim needs a diff or test log as support, it must be "
    "supplied in evidence."
)


def expect_program(
    bodies: list[Any], text: str, is_error: bool, _arguments: Mapping[str, Any], *, verify_hold: bool | str = False
) -> tuple[list[Any], str, bool]:
    rewritten = [_rewrite_body(body) for body in bodies]
    if is_error or not text.startswith("{"):
        return rewritten, text, is_error
    payload = json.loads(text)
    tool = payload.get("tool")
    if tool not in ("jev_find", "jev_gate", "jev_verify", "jev_review"):
        return bodies, text, is_error
    rewritten_bodies = rewritten
    evidence = _asked_evidence(rewritten_bodies)
    had_tests = any(
        isinstance(body, dict) and isinstance(body.get("state"), dict) and body["state"].get("tests")
        for body in rewritten_bodies
    )
    payload = _rewrite_payload(payload, tool, evidence, had_tests, rewritten_bodies, verify_hold)
    return rewritten_bodies, stringify(payload), is_error


def frozen_review_fields(actual: str, expected: str, arguments: Mapping[str, Any]) -> str:
    """Check the documented extensions; compare all other fields in frozen wire order."""
    if not expected.startswith("{"):
        return actual
    original = json.loads(expected)
    if original.get("tool") not in ("jev_review", "jev_gate") or "action" not in original:
        return actual
    observed = json.loads(actual)
    assert stringify(observed) == actual
    if original["tool"] == "jev_gate":
        blockers = observed.pop("blockers", [])
        rollup = observed.pop("rollup")
        assert rollup == {"action": original["action"], "reason_counts": dict(Counter(b["code"] for b in blockers))}
        assert bool(blockers) == (original["action"] != "auto")
        assert all(b["field"] and b["next_check"] for b in blockers)
        checks = observed.pop("next_checks")
        assert len(checks) == 1
        shape = diff_shape(arguments.get("diff"))
        expected_check = (
            "The row stands. Proceed."
            if original["action"] == "auto"
            else {"text": NO_HUNKS_CHECK, "excerpt": EXCERPT_CHECK}.get(shape)
        )
        assert checks[0] in [expected_check, *(b["next_check"] for b in blockers)]
        coverage = observed["review"].pop("citation_coverage")
        assert 0 <= coverage["offered"] <= min(32, coverage["available"])
        for row in observed["verification"]["results"]:
            if row.get("reported_verdict") == "contradicted":
                coverage = row.pop("citation_coverage")
                assert 0 <= coverage["offered"] <= min(32, coverage["available"])
    else:
        coverage = observed.pop("citation_coverage")
        assert 0 <= coverage["offered"] <= min(32, coverage["available"])
        if original["action"] != "auto":
            blockers = observed.pop("blockers")
            assert blockers and all(b["field"] and b["next_check"] for b in blockers)
    return stringify(observed)


def _rewrite_body(body: Any) -> Any:
    if not isinstance(body, dict) or "questions" not in body:
        return body
    rewritten = json.loads(json.dumps(body))
    questions = rewritten.get("questions")
    state = rewritten.get("state")
    if not isinstance(questions, dict):
        return rewritten
    for name, question in questions.items():
        if not isinstance(question, dict) or "instructions" not in question:
            continue
        instructions = str(question["instructions"])
        if name.startswith("claim_"):
            instructions = instructions.replace(_OLD_CLAIM, CLAIM_SUPPORT)
            if ROLE_RULE not in instructions and ANTI_INJECTION in instructions:
                instructions = instructions.replace(ANTI_INJECTION, ROLE_RULE + ANTI_INJECTION, 1)
        elif name.startswith("relation_") or name.startswith("source_"):
            if ANTI_INJECTION not in instructions:
                instructions += VERIFY_SUFFIX
        question["instructions"] = instructions
    if isinstance(state, dict) and "diff" in state and "claims" in state:
        evidence = list(state.get("evidence") or [])
        implicit: list[dict[str, object]] = []
        if state.get("diff"):
            implicit.append({"id": "diff", "text": state["diff"], "kind": "diff", "role": "after"})
        if state.get("tests"):
            implicit.append({"id": "tests", "text": state["tests"], "kind": "tool_output", "role": "current"})
        diff_index = len(evidence)
        evidence.extend(implicit)
        state["evidence"] = evidence
        ids = [str(item["id"]) for item in evidence]
        ordered: dict[str, Any] = {}
        for name, question in questions.items():
            ordered[name] = question
            if name.startswith("claim_"):
                index = int(name.removeprefix("claim_"))
                ordered[f"source_{index}"] = gate_source_question(index, ids).to_wire()
        questions.clear()
        questions.update(ordered)
        typed = review_questions(REVIEW_FRAMING)
        for i in range(len(state["claims"])):
            typed[f"claim_{i}"] = claim_question(i)
            typed[f"source_{i}"] = gate_source_question(i, ids)
        add_citations(state, typed, [evidence[diff_index]])
        add_citations(state, typed, evidence, claim_count=len(state["claims"]))
        questions.update({k: v for k, v in questions_to_wire(typed).items() if k not in questions})
    elif isinstance(state, dict) and state.get("purpose") == PURPOSE:
        typed = review_questions()
        add_citations(state, typed, [{"id": "diff", "kind": "diff", "text": state["diff"]}])
        questions.update({k: v for k, v in questions_to_wire(typed).items() if k not in questions})
    return rewritten


def _asked_evidence(bodies: list[Any]) -> list[dict[str, object]]:
    for body in bodies:
        if isinstance(body, dict) and isinstance(body.get("state"), dict):
            evidence = body["state"].get("evidence")
            if isinstance(evidence, list):
                return evidence
    return []


def _insert_after(obj: dict[str, Any], after: str, key: str, value: object) -> None:
    if key in obj:
        return
    rebuilt: dict[str, Any] = {}
    placed = False
    for name, item in obj.items():
        rebuilt[name] = item
        if name == after:
            rebuilt[key] = value
            placed = True
    if not placed:
        rebuilt[key] = value
    obj.clear()
    obj.update(rebuilt)


def _rewrite_payload(
    payload: dict[str, Any],
    tool: object,
    evidence: list[dict[str, object]],
    had_tests: bool,
    bodies: list[Any],
    verify_hold: bool | str,
) -> dict[str, Any]:
    has_defect_question = any("defect_kind" in body.get("questions", {}) for body in bodies if isinstance(body, dict))
    invalid_defect = False
    if tool == "jev_review" or "review" in payload:
        review = payload if tool == "jev_review" else payload.get("review")
        if isinstance(review, dict):
            invalid_defect = review["action"] == "auto" and has_defect_question
            if invalid_defect or (review["action"] == "escalate" and review.get("status") != "invalid_response"):
                review["action"] = "review"
            _annotate_review(review, had_tests)
    verification = payload.get("verification")
    if isinstance(verification, dict) and isinstance(verification.get("results"), list):
        rows = verification["results"]
        if tool == "jev_gate":
            review = payload["review"]
            # The recorded provider never answered any of the newly asked source or citation questions.
            invalid_source = any(row["action"] == "auto" for row in rows)
            for row in rows:
                if row.get("verdict") == "contradicted":
                    row.update(reported_verdict="contradicted", verdict="unsupported")
                if row["action"] == "auto" or (row["action"] == "escalate" and row.get("status") != "invalid_response"):
                    row["action"] = "review"
            order = ("auto", "review", "escalate")
            verification["action"] = max((row["action"] for row in rows), key=order.index)
            payload["action"] = max(review["action"], verification["action"], key=order.index)
            verification["summary"]["needs_review"] = sum(row["action"] != "auto" for row in rows)
            verification["summary"].update(
                {label: sum(row["verdict"] == label for row in rows) for label in ("contradicted", "unsupported")}
            )
            codes = set(payload["reason_codes"]) - {"claims_contradicted", "review_escalated", "accepted"}
            if any(row["verdict"] == "unsupported" for row in rows):
                codes.add("claims_unsupported")
            if review["action"] != "auto":
                codes.add("review_required" if review["action"] == "review" else "review_escalated")
            if invalid_source or invalid_defect:
                codes.add("invalid_response")
            if payload["action"] == "auto":
                codes.add("accepted")
            payload["reason_codes"] = [code for code in GATE_REASON_CODES if code in codes]
        _annotate_claims(rows, evidence)
        summary = verification.get("summary")
        if isinstance(summary, dict):
            summary.update(summary_extras(rows))
    if tool == "jev_verify" and isinstance(payload.get("results"), list):
        rows = payload["results"]
        if verify_hold:
            (row,) = rows
            row["action"] = "review"
            if isinstance(verify_hold, str):
                row["reason_codes"] = [verify_hold]
        _annotate_claims(rows, evidence)
        summary = payload.get("summary")
        if isinstance(summary, dict):
            summary["needs_review"] = sum(row["action"] == "review" for row in rows)
            summary.update(summary_extras(rows))
    return payload


def _annotate_claims(rows: list[Any], evidence: list[dict[str, object]]) -> None:
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        claim_id = str(row["id"]) if "id" in row else f"claim{index}"
        row.update(claim_extras(row, evidence, claim_id=None if "id" in row else claim_id))


def _annotate_review(review: dict[str, Any], had_tests: bool) -> None:
    scores = review.get("scores")
    if isinstance(scores, dict):
        for score in scores.values():
            if isinstance(score, dict) and "level" not in score:
                score["level"] = nearest_level(score.get("score"))
    if "weights" in review and "score_scale" not in review:
        _insert_after(review, "weights", "score_scale", list(SCORE_SCALE))
    if had_tests and "tests_weight" not in review:
        _insert_after(review, "composite", "tests_weight", "self_reported")
