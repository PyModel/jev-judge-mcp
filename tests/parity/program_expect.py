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
from jev_judge_mcp.ids import ensure_unique_ids
from jev_judge_mcp.responses import (
    SCORE_SCALE,
    caller_renames,
    claim_extras,
    diff_shape,
    nearest_level,
    next_checks_for,
    renamed_ids_field,
    review_saw_tests,
    summary_extras,
)
from jev_judge_mcp.serialize import stringify
from jev_judge_mcp.tools.common import evidence_items
from jev_judge_mcp.tools.gate import CLAIM_SUPPORT, REVIEW_FRAMING, claim_question, gate_source_question
from jev_judge_mcp.tools.gate_evidence import add_citations
from jev_judge_mcp.tools.review import ANTI_INJECTION
from jev_judge_mcp.tools.review_half import review_questions
from jev_judge_mcp.tools.verify import ROLE_RULE, VERIFY_SUFFIX

_OLD_CLAIM = (
    "Use only the evidence field as factual support; request and claims are assertions, not evidence; diff "
    "and tests belong to the separate patch review. If a claim needs a diff or test log as support, it must be "
    "supplied in evidence."
)


def expect_program(
    bodies: list[Any], text: str, is_error: bool, arguments: Mapping[str, Any]
) -> tuple[list[Any], str, bool]:
    rewritten = [_rewrite_body(body) for body in bodies]
    if is_error or not text.startswith("{"):
        return rewritten, text, is_error
    payload = json.loads(text)
    tool = payload.get("tool")
    if tool not in ("jev_find", "jev_gate", "jev_verify", "jev_review"):
        return bodies, text, is_error
    rewritten_bodies = [_rewrite_body(body) for body in bodies]
    evidence = _asked_evidence(rewritten_bodies)
    had_tests = any(
        isinstance(body, dict) and isinstance(body.get("state"), dict) and body["state"].get("tests")
        for body in rewritten_bodies
    )
    payload = _rewrite_payload(payload, tool, evidence, had_tests, rewritten_bodies, arguments)
    return rewritten_bodies, stringify(payload), is_error


def frozen_review_fields(actual: str, expected: str) -> str:
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
        assert observed["reason_codes"] == list(
            dict.fromkeys([*original["reason_codes"], *(b["code"] for b in blockers)])
        )
        assert len(observed["next_checks"]) == 1
        assert observed["next_checks"][0] in original["next_checks"] + [b["next_check"] for b in blockers]
        observed.update(reason_codes=original["reason_codes"], next_checks=original["next_checks"])
        coverage = observed["review"].pop("citation_coverage")
        assert 0 <= coverage["offered"] <= min(32, coverage["available"])
        for row in observed["verification"]["results"]:
            if row.get("reported_verdict") == "contradicted":
                coverage = row.pop("citation_coverage")
                assert 0 <= coverage["offered"] <= min(32, coverage["available"])
    elif original["action"] != "auto":
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
        evidence = ensure_unique_ids([*evidence, *implicit], "evidence").items
        state["evidence"] = evidence
        ids = [str(item["id"]) for item in evidence]
        if len(evidence) > 1:
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
            if len(evidence) > 1:
                typed[f"source_{i}"] = gate_source_question(i, ids)
        add_citations(state, typed, [implicit[0]])
        add_citations(state, typed, evidence, claim_count=len(state["claims"]))
        questions.update({k: v for k, v in questions_to_wire(typed).items() if k not in questions})
    return rewritten


def _asked_evidence(bodies: list[Any]) -> list[dict[str, object]]:
    for body in bodies:
        if isinstance(body, dict) and isinstance(body.get("state"), dict):
            evidence = body["state"].get("evidence")
            if isinstance(evidence, list):
                return evidence
    return []


def _asked_candidates(bodies: list[Any]) -> list[dict[str, object]]:
    for body in bodies:
        if isinstance(body, dict) and isinstance(body.get("state"), dict):
            candidates = body["state"].get("candidates")
            if isinstance(candidates, list):
                return candidates
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
    arguments: Mapping[str, Any],
) -> dict[str, Any]:
    if tool == "jev_review" or "review" in payload:
        review = payload if tool == "jev_review" else payload.get("review")
        if isinstance(review, dict):
            _annotate_review(review, had_tests)
    verification = payload.get("verification")
    if isinstance(verification, dict) and isinstance(verification.get("results"), list):
        if tool == "jev_gate":
            # The frozen provider never answered a citation question. Every old hold stays a hold.
            rows = verification["results"]
            review = payload["review"]
            if review["action"] == "escalate" and review.get("status") != "invalid_response":
                review["action"] = "review"
            for row in rows:
                if row.get("verdict") == "contradicted":
                    row.update(reported_verdict="contradicted", verdict="unsupported")
                if row["action"] == "escalate" and row.get("status") != "invalid_response":
                    row["action"] = "review"
            order = ("auto", "review", "escalate")
            verification["action"] = max((row["action"] for row in rows), key=order.index)
            payload["action"] = max(review["action"], verification["action"], key=order.index)
            verification["summary"].update(
                {label: sum(row["verdict"] == label for row in rows) for label in ("contradicted", "unsupported")}
            )
            codes = []
            for code in payload["reason_codes"]:
                if code == "claims_contradicted":
                    code = "claims_unsupported"
                if code == "review_escalated" and review["action"] == "review":
                    code = "review_required"
                if code not in codes:
                    codes.append(code)
            payload["reason_codes"] = codes
        _annotate_claims(verification["results"], evidence)
        summary = verification.get("summary")
        if isinstance(summary, dict):
            summary.update(summary_extras(verification["results"]))
    if tool == "jev_verify" and isinstance(payload.get("results"), list):
        _annotate_claims(payload["results"], evidence)
        summary = payload.get("summary")
        if isinstance(summary, dict):
            summary.update(summary_extras(payload["results"]))
    if tool == "jev_gate" and isinstance(payload.get("reason_codes"), list):
        _insert_after(
            payload,
            "reason_codes",
            "next_checks",
            next_checks_for(
                payload["reason_codes"],
                diff_shape=diff_shape(arguments.get("diff")),
                review_saw_tests=review_saw_tests(arguments),
            ),
        )
    _insert_renamed_ids(payload, tool, bodies, arguments)
    return payload


def _insert_renamed_ids(payload: dict[str, Any], tool: object, bodies: list[Any], arguments: Mapping[str, Any]) -> None:
    """ADR-0062 amendment: find, verify, and gate append `renamed_ids` when a caller id changed.

    The map is computed from the recorded arguments and the asked items on the wire, with the
    production builders, so the expectation cannot drift from the tool.
    """
    if tool == "jev_find":
        sent, asked = list(arguments.get("candidates") or []), _asked_candidates(bodies)
    elif tool in ("jev_verify", "jev_gate"):
        sent, asked = evidence_items(arguments.get("evidence")), _asked_evidence(bodies)
    else:
        return
    field = renamed_ids_field(caller_renames(sent, asked))
    if not field:
        return
    # Additive fields append after the body's last key, before the frame's usage and request_id.
    tail = {key: payload.pop(key) for key in ("usage", "request_id") if key in payload}
    payload.update(field)
    payload.update(tail)


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
