# pyright: reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnknownArgumentType=false, reportUnknownParameterType=false
"""Python expectation for the gate-program wire divergence.

The corpus stays the reference recording. This function adds the fields and question text
the server now sends, using the same builders, so a drift in an old key still fails.
"""

import json
from typing import Any

from jev_judge_mcp.ids import ensure_unique_ids
from jev_judge_mcp.responses import SCORE_SCALE, claim_extras, nearest_level, next_checks_for, summary_extras
from jev_judge_mcp.serialize import stringify
from jev_judge_mcp.tools.gate import CLAIM_SUPPORT, gate_source_question
from jev_judge_mcp.tools.review import ANTI_INJECTION
from jev_judge_mcp.tools.verify import ROLE_RULE, VERIFY_SUFFIX

_OLD_CLAIM = (
    "Use only the evidence field as factual support; request and claims are assertions, not evidence; diff "
    "and tests belong to the separate patch review. If a claim needs a diff or test log as support, it must be "
    "supplied in evidence."
)


def expect_program(bodies: list[Any], text: str, is_error: bool) -> tuple[list[Any], str, bool]:
    rewritten = [_rewrite_body(body) for body in bodies]
    if is_error or not text.startswith("{"):
        return rewritten, text, is_error
    payload = json.loads(text)
    tool = payload.get("tool")
    if tool not in ("jev_gate", "jev_verify", "jev_review"):
        return bodies, text, is_error
    rewritten_bodies = [_rewrite_body(body) for body in bodies]
    evidence = _asked_evidence(rewritten_bodies)
    had_tests = any(
        isinstance(body, dict) and isinstance(body.get("state"), dict) and body["state"].get("tests")
        for body in rewritten_bodies
    )
    return rewritten_bodies, stringify(_rewrite_payload(payload, tool, evidence, had_tests)), is_error


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
    payload: dict[str, Any], tool: object, evidence: list[dict[str, object]], had_tests: bool
) -> dict[str, Any]:
    if tool == "jev_review" or "review" in payload:
        review = payload if tool == "jev_review" else payload.get("review")
        if isinstance(review, dict):
            _annotate_review(review, had_tests)
    verification = payload.get("verification")
    if isinstance(verification, dict) and isinstance(verification.get("results"), list):
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
        _insert_after(payload, "reason_codes", "next_checks", next_checks_for(payload["reason_codes"]))
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
