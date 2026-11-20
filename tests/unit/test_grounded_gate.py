"""Completion escalations cite evidence from the actual gate input."""

import pytest

from tests.support.jev import call_tool, choice

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


REVIEW = {
    "correctness": {"score": 1.9, "confidence": 0.2},
    "spec_match": {"score": 2, "confidence": 1},
    "test_gap": {"score": 0, "confidence": 1},
    "blast_radius": {"score": 0, "confidence": 1},
    "safe_to_apply": {"noul": 0.4},
}
PATCH = "@@ -1 +1 @@\n-return n\n+return n + 2"
ARGS = {
    "request": "Double the argument",
    "diff": [{"path": "math.py", "patch": PATCH}],
    "claims": ["The tests passed"],
    "evidence": [{"id": "run", "kind": "tool_output", "text": "3 passed"}],
}
VERDICTS = ("verified", "unsupported", "contradicted")


async def test_confidence_and_an_uncited_contradiction_hold_for_one_specific_check() -> None:
    result = await call_tool("jev_gate", ARGS, {**REVIEW, "claim_0": choice("contradicted", *VERDICTS)})
    body = result.payload
    assert body["action"] == body["review"]["file_actions"]["math.py"] == "review"
    assert body["rollup"]["action"] == "review"
    assert body["rollup"]["reason_counts"]["claim_contradiction_unconfirmed"] == 1
    assert len(body["next_checks"]) == 1 and "claim0" in body["next_checks"][0]
    row = body["verification"]["results"][0]
    assert (row["verdict"], row["reported_verdict"], row["action"]) == ("unsupported", "contradicted", "review")


async def test_specific_defect_outranks_a_claim_hold_and_points_into_the_patch() -> None:
    answers = {
        **REVIEW,
        "claim_0": choice("verified", *VERDICTS),
        "defect_kind": choice(
            "wrong_result", "none", "wrong_result", "state_change", "error_handling", "safety_refusal"
        ),
        "defect_line": choice("L2", "none", "L1", "L2"),
    }
    body = (await call_tool("jev_gate", ARGS, answers)).payload
    assert body["action"] == "escalate"
    reason = next(row for row in body["blockers"] if row["code"] == "wrong_result")
    assert reason["evidence_line"] == {"source": "diff", "line": 3, "text": "+return n + 2"}
    assert reason["path"] == "math.py" and body["next_checks"] == [reason["next_check"]]


async def test_optional_path_over_wire_budget_names_the_context_and_still_checks_the_claim() -> None:
    path = "界" * 10_000
    args = {**ARGS, "diff": [{"path": path, "patch": "+return n"}]}
    body = (await call_tool("jev_gate", args, {**REVIEW, "claim_0": choice("verified", *VERDICTS)})).payload
    assert body["action"] == "review"
    assert body["review"]["context_omissions"][0]["omitted"] == ["path"]
    assert path in body["next_checks"][0]


async def test_corroborated_contradiction_uses_the_selected_line_instead_of_the_log_header() -> None:
    args = {**ARGS, "evidence": [{"id": "run", "kind": "tool_output", "text": "suite started\nFAIL test_double"}]}
    answers = {
        **REVIEW,
        "claim_0": choice("contradicted", *VERDICTS),
        "counterexample_0": choice("L3", "none", "L1", "L2", "L3"),
    }
    body = (await call_tool("jev_gate", args, answers)).payload
    row = body["verification"]["results"][0]
    assert row["action"] == body["rollup"]["action"] == "escalate"
    assert (row["verdict"], row["excerpt"]) == ("contradicted", "FAIL test_double")
    assert row["evidence_line"] == {"source": "run", "line": 2, "text": "FAIL test_double"}
    assert body["next_checks"] == [next(b["next_check"] for b in body["blockers"] if b["code"] == "claim_contradicted")]


async def test_a_passing_run_cannot_refute_a_plain_pass_claim_even_with_a_selected_line() -> None:
    answers = {
        **REVIEW,
        "claim_0": choice("contradicted", *VERDICTS),
        "counterexample_0": choice("L1", "none", "L1", "L2"),
    }
    body = (await call_tool("jev_gate", ARGS, answers)).payload
    assert body["verification"]["results"][0]["verdict"] == "unsupported"
    assert body["action"] == "review"


async def test_protocol_refusal_identifies_the_missing_provider_field() -> None:
    body = (await call_tool("jev_gate", ARGS, REVIEW)).payload
    blocker = next(b for b in body["blockers"] if b["code"] == "claim_invalid")
    assert blocker["invalid_answer"] == "claim_0"
    assert "evidence_line" not in blocker and "safety_refusal" not in blocker
    assert body["action"] == "escalate" and body["next_checks"] == [blocker["next_check"]]


async def test_documentation_review_points_to_one_statement_to_verify() -> None:
    args = {**ARGS, "diff": [{"path": "docs/double.md", "patch": "+The operation doubles its argument."}]}
    answers = {
        **REVIEW,
        "claim_0": choice("verified", *VERDICTS),
        "source_0": choice("run", "none", "run", "diff_docs_double.md"),
    }
    body = (await call_tool("jev_gate", args, answers)).payload
    (check,) = body["next_checks"]
    assert "docs/double.md" in check and "The operation doubles its argument." in check
    assert "Verify the changed statement" in check


@pytest.mark.parametrize(
    ("patch", "target"),
    [
        ("@@ -1 +1,2 @@\n-return n\n+import math\n+return n + 2", "patch line 4: +return n + 2"),
        ("@@ -1 +0,0 @@\n-if n < 0: return 0", "patch line 2: -if n < 0: return 0"),
    ],
)
async def test_code_review_next_check_identifies_a_changed_operation(patch: str, target: str) -> None:
    args = {**ARGS, "diff": [{"path": "math.py", "patch": patch}]}
    answers = {
        **REVIEW,
        "claim_0": choice("verified", *VERDICTS),
        "source_0": choice("run", "none", "run", "diff_math.py"),
    }
    body = (await call_tool("jev_gate", args, answers)).payload
    (check,) = body["next_checks"]
    assert body["action"] == "review"
    assert "math.py" in check and target in check


async def test_blank_patch_lines_cannot_establish_a_defect() -> None:
    args = {**ARGS, "diff": [{"path": "math.py", "patch": "@@ -1,1 +1,2 @@\n-   \n+\n+\t"}]}
    answers = {
        **REVIEW,
        "claim_0": choice("verified", *VERDICTS),
        "defect_kind": choice(
            "wrong_result", "none", "wrong_result", "state_change", "error_handling", "safety_refusal"
        ),
        "defect_line": choice("L2", "none", "L1", "L2", "L3"),
    }
    body = (await call_tool("jev_gate", args, answers)).payload
    assert body["review"]["citation_coverage"] == {"offered": 0, "available": 0}
    assert body["action"] == "review"


_GOOD_REVIEW = {**REVIEW, "correctness": {"score": 2, "confidence": 1}, "safe_to_apply": {"noul": 1}}
_DEFECTS = ("none", "wrong_result", "state_change", "error_handling", "safety_refusal")


@pytest.mark.parametrize("source", ["valid", "malformed", "missing"])
async def test_gate_source_question_cannot_clear_caller_note_review(source: str) -> None:
    args = {**ARGS, "evidence": [{"id": "run", "kind": "caller_note", "text": "Tests pass"}]}
    answers = {
        **_GOOD_REVIEW,
        "defect_kind": choice("none", *_DEFECTS),
        "claim_0": choice("verified", *VERDICTS),
    }
    if source != "missing":
        answers["source_0"] = choice("run" if source == "valid" else "unknown", "none", "run", "diff_math.py")
    body = (await call_tool("jev_gate", args, answers)).payload
    assert body["review"]["action"] == "auto"
    assert body["verification"]["results"][0]["action"] == body["action"] == "review"
    assert ("caller_note_only" in body["reason_codes"]) == (source == "valid")


@pytest.mark.parametrize("kind", ["valid", "malformed", "missing"])
async def test_gate_defect_kind_question_cannot_clear_review(kind: str) -> None:
    answers = {
        **_GOOD_REVIEW,
        "claim_0": choice("verified", *VERDICTS),
        "source_0": choice("run", "none", "run", "diff_math.py"),
    }
    if kind != "missing":
        answers["defect_kind"] = {
            **choice("wrong_result" if kind == "valid" else "unknown", *_DEFECTS),
            "confidence": 0.2,
        }
    review = (await call_tool("jev_gate", ARGS, answers)).payload["review"]
    assert review["file_actions"]["math.py"] == review["action"] == "review"


@pytest.mark.parametrize(
    ("patch", "selected", "target"),
    [
        ("@@ -1 +1 @@\n-old\n+new", "L2", {"line": 3, "text": "+new"}),
        (
            "--- a/x.sql\n+++ b/x.sql\n@@ -1 +1 @@\n--- x\n+++ i",
            "L1",
            {"line": 4, "text": "--- x"},
        ),
        (
            "--- a/x.sql\n+++ b/x.sql\n@@ -1 +1 @@\n--- x\n+++ i",
            "L2",
            {"line": 5, "text": "+++ i"},
        ),
    ],
)
async def test_string_gate_cites_the_implicit_diff_and_hunk_changes(
    patch: str, selected: str, target: dict[str, object]
) -> None:
    args = {**ARGS, "diff": patch, "evidence": [{"id": "caller", "text": "Caller item."}]}
    answers = {
        **REVIEW,
        "claim_0": choice("verified", *VERDICTS),
        "defect_kind": choice("wrong_result", *_DEFECTS),
        "defect_line": choice(selected, "none", "L1", "L2"),
    }
    body = (await call_tool("jev_gate", args, answers)).payload
    blocker = next((row for row in body["blockers"] if row["code"] == "wrong_result"), None)
    assert blocker is not None, body["blockers"]
    assert blocker["evidence_line"] == {"source": "diff", **target}


async def test_protocol_blocker_has_one_shape_and_only_real_refusal_sets_flag() -> None:
    body = (
        await call_tool(
            "jev_gate",
            ARGS,
            {
                **REVIEW,
                "defect_kind": choice("safety_refusal", *_DEFECTS),
                "defect_line": choice("L2", "none", "L1", "L2"),
            },
        )
    ).payload
    invalid = next(b for b in body["blockers"] if b["code"] == "claim_invalid")
    refusal = next(b for b in body["blockers"] if b["code"] == "safety_refusal")
    assert "safety_refusal" not in invalid and "evidence_line" not in invalid
    assert invalid["invalid_answer"] == "claim_0"
    assert refusal["safety_refusal"] is True
    assert set(refusal["evidence_line"]) == {"source", "line", "text"}


async def test_gate_summary_codes_keep_the_frozen_vocabulary() -> None:
    body = (await call_tool("jev_gate", ARGS, REVIEW)).payload
    assert body["reason_codes"] == ["invalid_response", "review_required"]
    assert body["rollup"]["reason_counts"]["claim_invalid"] == 1


@pytest.mark.parametrize("file_list", [False, True], ids=["string", "file-list"])
async def test_review_holds_uncited_concerns_and_anchors_the_changed_line(file_list: bool) -> None:
    args = {"request": ARGS["request"], "diff": ARGS["diff"] if file_list else PATCH}
    body = (await call_tool("jev_review", args, REVIEW)).payload
    assert body["action"] == "review"
    blocker = next(b for b in body["blockers"] if b["field"] == "correctness.confidence")
    assert "patch line 3: +return n + 2" in blocker["next_check"]


async def test_nonverifying_claim_never_reports_caller_note_only() -> None:
    args = {**ARGS, "evidence": [{"id": "run", "kind": "caller_note", "text": "Tests pass"}]}
    answers = {
        **_GOOD_REVIEW,
        "defect_kind": choice("none", *_DEFECTS),
        "claim_0": choice("unsupported", *VERDICTS),
        "source_0": choice("run", "none", "run", "diff_math.py"),
    }
    body = (await call_tool("jev_gate", args, answers)).payload
    assert body["reason_codes"] == ["claims_unsupported"]


@pytest.mark.parametrize(("source", "expected"), [("none", "review"), ("tests", "auto")])
async def test_verified_claim_requires_a_selected_source(source: str, expected: str) -> None:
    answers = {
        **_GOOD_REVIEW,
        "defect_kind": choice("none", *_DEFECTS),
        "claim_0": choice("verified", *VERDICTS),
        "source_0": choice(source, "none", "run", "diff_math.py", "tests"),
    }
    body = (await call_tool("jev_gate", {**ARGS, "tests": "pytest: 3 passed"}, answers)).payload
    row = body["verification"]["results"][0]
    assert body["action"] == row["action"] == expected
    assert row["supporting_evidence"] == (None if source == "none" else source)
    assert ("claim_verified_without_source" in body["reason_codes"]) == (source == "none")
