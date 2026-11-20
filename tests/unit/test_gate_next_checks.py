"""Deterministic gate diagnostics at the tool boundary."""

import pytest

from jev_judge_mcp.responses import EXCERPT_CHECK, NO_HUNKS_CHECK, diff_shape
from tests.support.jev import NO_DEFECT, call_tool, choice

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


_REVIEW = {
    "correctness": {"score": 2, "confidence": 0.95},
    "spec_match": {"score": 2, "confidence": 0.95},
    "test_gap": {"score": 0, "confidence": 0.95},
    "blast_radius": {"score": 0, "confidence": 0.95},
    "safe_to_apply": {"noul": 0.95},
    "defect_kind": NO_DEFECT,
}
_UNSUPPORTED = {
    "claim_0": {
        "choice": "unsupported",
        "confidence": 0.9,
        "probabilities": {"verified": 0.05, "contradicted": 0.05, "unsupported": 0.9},
    },
}
_VERIFIED = {
    "claim_0": {
        "choice": "verified",
        "confidence": 0.95,
        "probabilities": {"verified": 0.95, "contradicted": 0.03, "unsupported": 0.02},
    },
}
_SUMMARY = "Added the cache layer and fixed the parser; tests pass."
_PATCH = "diff --git a/p.py b/p.py\n--- a/p.py\n+++ b/p.py\n@@ -1 +1 @@\n-old\n+new\n"
_TWO_FILES = (
    "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1,3 +1,3 @@ def f():\n ctx\n-old\n+new\n ctx\n"
    "\\ No newline at end of file\n"
    "diff --git a/b.py b/b.py\n--- a/b.py\n+++ b/b.py\n@@ -0,0 +1,2 @@\n+one\n+two\n"
)
_ELIDED = "diff --git a/p.py b/p.py\n--- a/p.py\n+++ b/p.py\n@@ -10,40 +10,52 @@\n def f():\n+    x = 1\n     ...\n"
_COUNTLESS = "--- a/p.py\n+++ b/p.py\n@@ ... @@\n-old\n+new\n"
_REVIEW_LOW = {**_REVIEW, "correctness": {"score": 1, "confidence": 0.3}, "safe_to_apply": {"noul": 0.2}}


def _neutral_source(*ids: str) -> dict[str, object]:
    return choice("none", "none", "evidence", *ids)


def test_diff_shape_is_computed_from_the_argument() -> None:
    assert diff_shape([{"path": "a", "patch": "+x"}]) == "file_list"
    assert diff_shape(_PATCH) == "patch"
    assert diff_shape("+x") == "text"
    assert diff_shape(_SUMMARY) == "text"
    assert diff_shape("- added caching\n- fixed the parser") == "text"  # bullets are not removals
    assert diff_shape(None) == "text"
    assert diff_shape(_TWO_FILES) == "patch"  # counts hold across files, context, and the no-newline marker
    assert diff_shape(_TWO_FILES.replace("\n", "\r\n")) == "patch"  # pasted with CRLF endings
    blank_context = "--- a/f\n+++ b/f\n@@ -1,2 +1,2 @@\n\n-old\n+new\n"  # an editor stripped the space
    assert diff_shape(blank_context) == "patch"
    assert diff_shape(blank_context.replace("\n", "\r\n")) == "patch"
    assert diff_shape(_PATCH.replace("-old\n", "-old\n\n", 1)) == "excerpt"  # the extra blank is one line too many
    assert diff_shape("@@\n-old\n+new") == "text"
    assert diff_shape(_ELIDED) == "excerpt"  # the header promises 40/52 lines; three follow
    assert diff_shape(_PATCH.replace("@@ -1 +1", "@@ -\uff11 +\uff11")) == "excerpt"
    assert diff_shape(_COUNTLESS) == "excerpt"  # a header git never writes
    assert diff_shape(_PATCH + "+stray line past the hunk\n") == "excerpt"
    assert diff_shape([{"path": "a", "patch": _PATCH}, {"path": "b", "patch": _ELIDED}]) == "excerpt"
    for separator in ("\x0c", "\u2028", "\x1c", "\x85"):  # line breaks to str.splitlines, not to git
        real = f"--- a/f.c\n+++ b/f.c\n@@ -0,0 +1,2 @@\n+int a;{separator}int b;\n+int c;\n"
        assert diff_shape(real) == "patch", repr(separator)


@pytest.mark.parametrize(
    ("diff", "hint"),
    [
        (_SUMMARY, NO_HUNKS_CHECK),
        ("@@ Notes\n+ Added the cache layer", NO_HUNKS_CHECK),
        (_PATCH, None),
        ([{"path": "p.py", "patch": _PATCH}], None),
        (_ELIDED, EXCERPT_CHECK),
    ],
)
async def test_the_hint_follows_the_diff_structure(diff: object, hint: str | None) -> None:
    args = {"request": "add the cache", "diff": diff, "claims": ["the cache is wired"], "evidence": "x"}
    result = await call_tool("jev_gate", args, {**_REVIEW_LOW, **_UNSUPPORTED})
    (check,) = result.payload["next_checks"]
    if hint is None:
        assert check not in (NO_HUNKS_CHECK, EXCERPT_CHECK)
    else:
        assert check == hint


@pytest.mark.parametrize("verified", [True, False])
async def test_review_and_claim_holds_name_the_blocking_check(verified: bool) -> None:
    args = {"request": "add the cache", "diff": _PATCH, "claims": ["the cache is wired"], "evidence": "x"}
    answers = {**_REVIEW_LOW, **(_VERIFIED if verified else _UNSUPPORTED), "source_0": _neutral_source("diff")}
    result = await call_tool("jev_gate", args, answers)
    (check,) = result.payload["next_checks"]
    assert ("claim0" in check) is not verified
    assert check in [b["next_check"] for b in result.payload["blockers"]]


@pytest.mark.parametrize("diff", [_SUMMARY, "+ Added the cache layer"])
async def test_a_gate_over_a_summary_names_the_missing_hunks(diff: str) -> None:
    args = {"request": "add the cache", "diff": diff, "claims": ["the cache is wired"], "evidence": "summary"}
    outcome = await call_tool("jev_gate", args, {**_REVIEW, **_UNSUPPORTED})
    assert not outcome.is_error, outcome.text
    assert outcome.payload["action"] != "auto"
    assert outcome.payload["next_checks"][-1] == NO_HUNKS_CHECK
    assert "claims_unsupported" in outcome.payload["reason_codes"]
    (blocker,) = outcome.payload["blockers"]
    assert (blocker["claim_id"], blocker["value"], blocker["confidence"]) == ("claim0", "unsupported", 0.9)
    assert outcome.payload["next_checks"] == [NO_HUNKS_CHECK]


async def test_high_confidence_adverse_levels_explain_a_composite_only_hold() -> None:
    args = {"request": "add the cache", "diff": _PATCH, "claims": ["the cache is wired"], "evidence": "x = 1 set"}
    answers = {
        **_REVIEW,
        **_VERIFIED,
        "source_0": _neutral_source("diff"),
        "correctness": {"score": 0, "confidence": 0.99},
        "test_gap": {"score": 2, "confidence": 0.99},
    }
    outcome = await call_tool("jev_gate", args, answers)
    assert outcome.payload["action"] == "review"
    (blocker,) = outcome.payload["blockers"]
    assert blocker["code"] == "composite_below_floor"
    assert blocker["value"] == pytest.approx(0.45)
    assert blocker["threshold"] == 0.7
    assert {k for k, row in blocker["rubrics"].items() if row["level_assessment"] == "adverse"} == {
        "correctness",
        "test_gap",
    }
    assert blocker["next_check"] in outcome.payload["next_checks"]


async def test_a_real_patch_or_an_accepted_call_carries_no_hint() -> None:
    args = {"request": "add the cache", "diff": _PATCH, "claims": ["the cache is wired"], "evidence": "summary"}
    unsupported = await call_tool("jev_gate", args, {**_REVIEW, **_UNSUPPORTED})
    assert NO_HUNKS_CHECK not in unsupported.payload["next_checks"]
    accepted = await call_tool(
        "jev_gate", {**args, "diff": _SUMMARY}, {**_REVIEW, **_VERIFIED, "source_0": _neutral_source("diff")}
    )
    assert accepted.payload["action"] == "auto", accepted.text
    assert NO_HUNKS_CHECK not in accepted.payload["next_checks"]


async def test_a_gate_over_a_trimmed_patch_says_the_review_scored_the_excerpt() -> None:
    args = {"request": "add the cache", "diff": _ELIDED, "claims": ["the cache is wired"], "evidence": "x = 1 set"}
    outcome = await call_tool("jev_gate", args, {**_REVIEW_LOW, **_VERIFIED})
    assert not outcome.is_error, outcome.text
    assert outcome.payload["action"] == "review"
    assert outcome.payload["next_checks"] == [EXCERPT_CHECK]


async def test_a_file_list_gate_checks_each_patch_for_trimming() -> None:
    diff = [{"path": "p.py", "patch": _PATCH}, {"path": "q.py", "patch": _ELIDED}]
    args = {"request": "add the cache", "diff": diff, "claims": ["the cache is wired"], "evidence": "x = 1 set"}
    outcome = await call_tool("jev_gate", args, {**_REVIEW_LOW, **_VERIFIED})
    assert not outcome.is_error, outcome.text
    assert EXCERPT_CHECK in outcome.payload["next_checks"]


@pytest.mark.parametrize("tests", [None, "", "5 passed"])
async def test_the_dedicated_test_log_is_distinguished_from_general_evidence(tests: str | None) -> None:
    args = {
        "request": "add the cache",
        "diff": _PATCH,
        "claims": ["the cache is wired"],
        "evidence": [{"id": "ci", "text": "5 passed", "kind": "tool_output"}],
    }
    if tests is not None:
        args["tests"] = tests
    result = await call_tool("jev_gate", args, {**_REVIEW_LOW, **_UNSUPPORTED})
    assert (result.payload["review"].get("tests_weight") == "self_reported") is bool(tests)


async def test_a_gate_with_test_output_in_evidence_addresses_the_reported_hold() -> None:
    log = {"id": "ci", "text": "5 passed", "kind": "tool_output"}
    args = {"request": "add the cache", "diff": _PATCH, "claims": ["the cache is wired"], "evidence": [log]}
    outcome = await call_tool(
        "jev_gate",
        args,
        {**_REVIEW_LOW, **_VERIFIED, "source_0": {"choice": "ci", "probabilities": {"ci": 1, "none": 0, "diff": 0}}},
    )
    assert not outcome.is_error, outcome.text
    assert outcome.payload["action"] == "review"
    assert outcome.payload["next_checks"] == [outcome.payload["blockers"][0]["next_check"]]


@pytest.mark.parametrize("diff", [_PATCH, [{"path": "p.py", "patch": _PATCH}]], ids=["string", "file_list"])
async def test_a_gate_that_sent_tests_to_the_review_does_not_ask_for_them_again(diff: object) -> None:
    """The same text fits a string diff, which has no file_actions, and a file list, which does."""
    args = {
        "request": "add the cache",
        "diff": diff,
        "tests": "5 passed",
        "claims": ["the cache is wired"],
        "evidence": "x",
    }
    source = _neutral_source("diff" if isinstance(diff, str) else "diff_p.py", "tests")
    outcome = await call_tool("jev_gate", args, {**_REVIEW_LOW, **_VERIFIED, "source_0": source})
    assert not outcome.is_error, outcome.text
    assert len(outcome.payload["next_checks"]) == 1
    assert "failure and recovery" in outcome.payload["next_checks"][0]
