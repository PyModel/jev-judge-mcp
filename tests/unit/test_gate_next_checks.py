"""The gate's no-hunks hint (ADR-0062 amendment): a text `diff` that did not stand is told so in code.

The reason codes and their fixed checks stay the parity suite's job; this pins the one deterministic
line the string path appends, when it appears, and when it must not.
"""

import pytest

from jev_judge_mcp.responses import NO_HUNKS_CHECK, diff_shape, next_checks_for
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


def test_diff_shape_is_computed_from_the_argument() -> None:
    assert diff_shape([{"path": "a", "patch": "+x"}]) == "file_list"
    assert diff_shape(_PATCH) == "patch"
    assert diff_shape("+x") == "patch"  # the minimal patch the fixtures use
    assert diff_shape(_SUMMARY) == "text"
    assert diff_shape("- added caching\n- fixed the parser") == "text"  # bullets are not removals
    assert diff_shape(None) == "text"


def test_the_hint_rides_only_on_a_text_diff_that_did_not_stand() -> None:
    assert NO_HUNKS_CHECK in next_checks_for(["claims_unsupported"], diff_shape="text")
    assert NO_HUNKS_CHECK not in next_checks_for(["accepted"], diff_shape="text")
    assert NO_HUNKS_CHECK not in next_checks_for(["claims_unsupported"], diff_shape="patch")
    assert NO_HUNKS_CHECK not in next_checks_for(["claims_unsupported"], diff_shape="file_list")
    assert next_checks_for(["claims_unsupported"]) == next_checks_for(["claims_unsupported"], diff_shape=None)


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
