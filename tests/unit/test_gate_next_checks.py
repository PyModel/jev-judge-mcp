"""The gate's computed hints (ADR-0062 amendments): a text `diff` or a hand-trimmed patch that did not
stand is told so in code, and so is a call whose claims all stand while its patch review did not.

The reason codes and their fixed checks stay the parity suite's job; this pins the deterministic lines
appended after them, when they appear, and when they must not.
"""

import pytest

from jev_judge_mcp.responses import EXCERPT_CHECK, NO_HUNKS_CHECK, REVIEW_ONLY_CHECK, diff_shape, next_checks_for
from tests.support.jev import call_tool

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


def test_diff_shape_is_computed_from_the_argument() -> None:
    assert diff_shape([{"path": "a", "patch": "+x"}]) == "file_list"
    assert diff_shape(_PATCH) == "patch"
    assert diff_shape("+x") == "patch"  # the minimal patch the fixtures use
    assert diff_shape(_SUMMARY) == "text"
    assert diff_shape("- added caching\n- fixed the parser") == "text"  # bullets are not removals
    assert diff_shape(None) == "text"
    assert diff_shape(_TWO_FILES) == "patch"  # counts hold across files, context, and the no-newline marker
    assert diff_shape(_TWO_FILES.replace("\n", "\r\n")) == "patch"  # pasted with CRLF endings
    blank_context = "--- a/f\n+++ b/f\n@@ -1,2 +1,2 @@\n\n-old\n+new\n"  # an editor stripped the space
    assert diff_shape(blank_context) == "patch"
    assert diff_shape(blank_context.replace("\n", "\r\n")) == "patch"
    assert diff_shape(_PATCH.replace("-old\n", "-old\n\n", 1)) == "excerpt"  # the extra blank is one line too many
    assert diff_shape("@@\n-old\n+new") == "patch"  # a bare marker carries no counts to check
    assert diff_shape(_ELIDED) == "excerpt"  # the header promises 40/52 lines; three follow
    assert diff_shape(_COUNTLESS) == "excerpt"  # a header git never writes
    assert diff_shape(_PATCH + "+stray line past the hunk\n") == "excerpt"
    assert diff_shape([{"path": "a", "patch": _PATCH}, {"path": "b", "patch": _ELIDED}]) == "excerpt"
    for separator in ("\x0c", "\u2028", "\x1c", "\x85"):  # line breaks to str.splitlines, not to git
        real = f"--- a/f.c\n+++ b/f.c\n@@ -0,0 +1,2 @@\n+int a;{separator}int b;\n+int c;\n"
        assert diff_shape(real) == "patch", repr(separator)


def test_the_hint_rides_only_on_a_text_diff_that_did_not_stand() -> None:
    assert NO_HUNKS_CHECK in next_checks_for(["claims_unsupported"], diff_shape="text")
    assert NO_HUNKS_CHECK not in next_checks_for(["accepted"], diff_shape="text")
    assert NO_HUNKS_CHECK not in next_checks_for(["claims_unsupported"], diff_shape="patch")
    assert NO_HUNKS_CHECK not in next_checks_for(["claims_unsupported"], diff_shape="file_list")
    assert next_checks_for(["claims_unsupported"]) == next_checks_for(["claims_unsupported"], diff_shape=None)
    assert EXCERPT_CHECK in next_checks_for(["review_escalated"], diff_shape="excerpt")
    assert EXCERPT_CHECK not in next_checks_for(["accepted"], diff_shape="excerpt")
    assert NO_HUNKS_CHECK not in next_checks_for(["review_escalated"], diff_shape="excerpt")


def test_the_review_only_hint_needs_every_claim_standing() -> None:
    assert REVIEW_ONLY_CHECK in next_checks_for(["review_escalated"])
    assert REVIEW_ONLY_CHECK in next_checks_for(["incomplete_context", "review_required"])
    for claim_code in ("claims_unsupported", "claims_contradicted", "claim_confidence_low", "invalid_response"):
        assert REVIEW_ONLY_CHECK not in next_checks_for(["review_escalated", claim_code])
    assert REVIEW_ONLY_CHECK not in next_checks_for(["claims_unsupported"])
    assert REVIEW_ONLY_CHECK not in next_checks_for(["accepted"])


async def test_a_gate_over_a_summary_names_the_missing_hunks() -> None:
    args = {"request": "add the cache", "diff": _SUMMARY, "claims": ["the cache is wired"], "evidence": "summary"}
    outcome = await call_tool("jev_gate", args, {**_REVIEW, **_UNSUPPORTED})
    assert not outcome.is_error, outcome.text
    assert outcome.payload["action"] != "auto"
    assert outcome.payload["next_checks"][-1] == NO_HUNKS_CHECK
    assert "claims_unsupported" in outcome.payload["reason_codes"]  # the codes are untouched


async def test_a_real_patch_or_an_accepted_call_carries_no_hint() -> None:
    args = {"request": "add the cache", "diff": _PATCH, "claims": ["the cache is wired"], "evidence": "summary"}
    unsupported = await call_tool("jev_gate", args, {**_REVIEW, **_UNSUPPORTED})
    assert NO_HUNKS_CHECK not in unsupported.payload["next_checks"]
    accepted = await call_tool("jev_gate", {**args, "diff": _SUMMARY}, {**_REVIEW, **_VERIFIED})
    assert accepted.payload["action"] == "auto", accepted.text
    assert NO_HUNKS_CHECK not in accepted.payload["next_checks"]


async def test_a_gate_over_a_trimmed_patch_says_the_review_scored_the_excerpt() -> None:
    args = {"request": "add the cache", "diff": _ELIDED, "claims": ["the cache is wired"], "evidence": "x = 1 set"}
    outcome = await call_tool("jev_gate", args, {**_REVIEW_LOW, **_VERIFIED})
    assert not outcome.is_error, outcome.text
    assert outcome.payload["action"] == "escalate"
    assert outcome.payload["next_checks"][-2:] == [REVIEW_ONLY_CHECK, EXCERPT_CHECK]


async def test_a_file_list_gate_checks_each_patch_for_trimming() -> None:
    diff = [{"path": "p.py", "patch": _PATCH}, {"path": "q.py", "patch": _ELIDED}]
    args = {"request": "add the cache", "diff": diff, "claims": ["the cache is wired"], "evidence": "x = 1 set"}
    outcome = await call_tool("jev_gate", args, {**_REVIEW_LOW, **_VERIFIED})
    assert not outcome.is_error, outcome.text
    assert EXCERPT_CHECK in outcome.payload["next_checks"]
