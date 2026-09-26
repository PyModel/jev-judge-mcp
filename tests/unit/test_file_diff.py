"""A file list is not silently cut, and an unreviewed file is not auto."""

import json
from collections.abc import Mapping
from typing import Any, cast, override

import pytest
from mcp.types import CallToolResult, TextContent

from jev_judge_mcp.domain import JsonValue, Usage
from jev_judge_mcp.limits import GATE
from jev_judge_mcp.providers import Evaluation
from jev_judge_mcp.settings import Settings
from jev_judge_mcp.tools import TOOLS, Runtime, Toolset
from tests.support.jev import FakeProvider, Outcome, call_tool

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


async def test_an_oversized_file_is_unreviewed_and_not_auto() -> None:
    outcome = await call_tool(
        "jev_review",
        {
            "request": "fix the parser",
            "diff": [{"path": "src/parser.py", "patch": "x" * (GATE.doc_units + 1)}],
        },
        {},
    )
    assert not outcome.is_error, outcome.text
    assert outcome.payload["partial"] is True
    assert outcome.payload["unreviewed_files"] == ["src/parser.py"]
    assert outcome.payload["action"] != "auto"
    assert outcome.requests == []


async def test_two_files_under_the_cap_are_reviewed_even_if_the_join_is_not() -> None:
    """A joined string over the cap must not become one silent cut. Each file is asked."""
    half = "y" * (GATE.doc_units // 2 + 1)
    outcome = await call_tool(
        "jev_review",
        {
            "request": "fix the parser",
            "diff": [
                {"path": "src/a.py", "patch": half},
                {"path": "src/b.py", "patch": half},
            ],
        },
        {
            "correctness": {"score": 2, "confidence": 0.95},
            "spec_match": {"score": 2, "confidence": 0.95},
            "test_gap": {"score": 0, "confidence": 0.95},
            "blast_radius": {"score": 0, "confidence": 0.95},
            "safe_to_apply": {"noul": 0.95},
        },
    )
    assert not outcome.is_error, outcome.text
    assert outcome.payload["unreviewed_files"] == []
    assert outcome.payload["partial"] is False
    assert len(outcome.requests) == 2
    assert outcome.payload["action"] == "auto"
    assert outcome.payload["score_file"] == "src/a.py"
    assert outcome.payload["reviewed_files"] == ["src/a.py", "src/b.py"]


_REVIEW_ANSWERS = {
    "correctness": {"score": 2, "confidence": 0.95},
    "spec_match": {"score": 2, "confidence": 0.95},
    "test_gap": {"score": 0, "confidence": 0.95},
    "blast_radius": {"score": 0, "confidence": 0.95},
    "safe_to_apply": {"noul": 0.95},
}

_GATE_ANSWERS = {
    **_REVIEW_ANSWERS,
    "claim_0": {
        "choice": "verified",
        "confidence": 0.95,
        "probabilities": {"verified": 0.95, "contradicted": 0.03, "unsupported": 0.02},
    },
}


async def _call_gate_raw(diff: object, evidence: list[dict[str, str]]) -> tuple[CallToolResult, FakeProvider]:
    """Call jev_gate through the real toolset; the caller asserts on the raw result blocks."""
    provider = FakeProvider({})
    toolset = Toolset(Runtime(Settings(), provider_factory=lambda _: provider), TOOLS)
    try:
        result = await toolset.call(
            "jev_gate",
            {
                "request": "fix the parser",
                "diff": diff,
                "claims": ["it works"],
                "evidence": evidence,
            },
        )
    finally:
        await toolset.aclose()
    return result, provider


_OVER_ITEMS = [{"id": f"e{index}", "text": "line"} for index in range(GATE.evidence_items + 1)]
_OVER_AGGREGATE = [{"id": f"e{index}", "text": "x" * 100_001} for index in range(2)]


async def test_a_file_list_over_the_evidence_budget_refuses_like_a_string_diff() -> None:
    """The evidence budgets are refused before the split, so a file list keeps the isError shape.

    The split path once rechecked the budgets per file and rebuilt a success-flagged result from
    the refusal payload; a client keying on isError saw a gate that decided nothing. The
    aggregate refusal carries the `input_too_large` code; the item-count refusal keeps whatever
    code the string path gives it, byte-for-byte.
    """
    for evidence, expected_error, expected_code in (
        (_OVER_ITEMS, "evidence exceeds 16 items; split the gate or trim the evidence.", None),
        (
            _OVER_AGGREGATE,
            "evidence exceeds the 200,000-character aggregate budget; split the gate or trim the evidence.",
            "input_too_large",
        ),
    ):
        string_result, string_provider = await _call_gate_raw("+ fix", evidence)
        list_result, list_provider = await _call_gate_raw(
            [{"path": "src/a.py", "patch": "+ a"}, {"path": "src/b.py", "patch": "+ b"}], evidence
        )
        assert string_result.is_error
        assert list_result.is_error
        assert string_provider.requests == []
        assert list_provider.requests == []
        string_payload = json.loads(cast(TextContent, string_result.content[0]).text)
        list_payload = json.loads(cast(TextContent, list_result.content[0]).text)
        assert list_payload == string_payload == {"tool": "jev_gate", "error": expected_error}
        string_code = json.loads(cast(TextContent, string_result.content[1]).text)
        list_code = json.loads(cast(TextContent, list_result.content[1]).text)
        assert list_code == string_code
        assert list_result.structured_content == string_result.structured_content
        if expected_code is not None:
            assert list_code == {"code": expected_code}
            assert list_result.structured_content == {"code": expected_code}


async def test_file_list_gate_sums_provider_usage_and_keeps_the_request_id() -> None:
    """Three gate asks (two file reviews, one claims verification) bill three provider calls."""
    outcome = await call_tool(
        "jev_gate",
        {
            "request": "fix the parser",
            "diff": [
                {"path": "mathutil.py", "patch": "+ add"},
                {"path": "limits.py", "patch": "+ cap"},
            ],
            "claims": ["both files changed"],
            "evidence": [{"id": "log", "text": "2 passed"}],
        },
        _GATE_ANSWERS,
        request_id="req-gate",
    )
    assert not outcome.is_error, outcome.text
    assert len(outcome.requests) == 3
    assert outcome.payload["usage"] == {"input_tokens": 3, "output_tokens": 3}
    assert outcome.payload["request_id"] == "req-gate"


async def test_file_list_review_keeps_provider_usage_and_unhashed_tests() -> None:
    """A two-file review frames the evaluation. Unhashed text tests are self-reported."""
    from tests.support.jev import call_tool as call

    outcome = await call(
        "jev_review",
        {
            "request": "fix the parser",
            "diff": [
                {"path": "mathutil.py", "patch": "+ add"},
                {"path": "limits.py", "patch": "+ cap"},
            ],
            "tests": "1 passed",
        },
        _REVIEW_ANSWERS,
        request_id="req-file",
    )
    assert not outcome.is_error, outcome.text
    assert outcome.payload["provider"] == "compatible"
    assert outcome.payload["usage"] == {"input_tokens": 2, "output_tokens": 2}
    assert outcome.payload["request_id"] == "req-file"
    assert outcome.payload["tests_weight"] == "self_reported"
    hashed = await call(
        "jev_review",
        {
            "request": "fix the parser",
            "diff": [
                {"path": "mathutil.py", "patch": "+ add"},
                {"path": "limits.py", "patch": "+ cap"},
            ],
            "tests": "1 passed",
            "tests_sha256": "abc",
        },
        _REVIEW_ANSWERS,
    )
    assert "tests_weight" not in hashed.payload
    assert hashed.payload["provider"] == "compatible"


async def test_gate_reviews_a_file_list_per_file() -> None:
    outcome = await call_tool(
        "jev_gate",
        {
            "request": "fix the parser",
            "diff": [
                {"path": "src/a.py", "patch": "+ a"},
                {"path": "src/b.py", "patch": "+ b"},
            ],
            "claims": ["both files changed"],
            "evidence": [{"id": "log", "text": "2 passed"}],
        },
        {
            "correctness": {"score": 2, "confidence": 0.95},
            "spec_match": {"score": 2, "confidence": 0.95},
            "test_gap": {"score": 0, "confidence": 0.95},
            "blast_radius": {"score": 0, "confidence": 0.95},
            "safe_to_apply": {"noul": 0.95},
            "claim_0": {
                "choice": "verified",
                "confidence": 0.95,
                "probabilities": {"verified": 0.95, "contradicted": 0.03, "unsupported": 0.02},
            },
        },
    )
    assert not outcome.is_error, outcome.text
    assert outcome.payload["partial"] is False
    assert outcome.payload["unreviewed_files"] == []
    assert len(outcome.requests) == 3


async def test_file_list_gate_asks_the_claims_once_not_per_file() -> None:
    """J4: each fitting file is asked the review rubric alone; the claims and evidence are sent once.

    The split path once re-asked the full rubric plus every claim and source question per file,
    billing N requests that each carried the whole evidence and discarding N-1 claim verdicts.
    """

    def state_of(request: tuple[JsonValue, dict[str, JsonValue]]) -> dict[str, JsonValue]:
        state = request[0]
        assert isinstance(state, dict)
        return state

    outcome = await call_tool(
        "jev_gate",
        {
            "request": "fix the parser",
            "diff": [
                {"path": "mathutil.py", "patch": "+ add"},
                {"path": "limits.py", "patch": "+ cap"},
            ],
            "claims": ["both files changed"],
            "evidence": [{"id": "log", "text": "2 passed"}],
        },
        _GATE_ANSWERS,
    )
    assert not outcome.is_error, outcome.text
    assert len(outcome.requests) == 3
    file_reviews = [request for request in outcome.requests if "claims" not in state_of(request)]
    assert len(file_reviews) == 2
    for request in file_reviews:
        state = state_of(request)
        assert "diff" in state
        assert "evidence" not in state
        assert not any(key.startswith(("claim_", "source_")) for key in request[1])
    verification_request = next(request for request in outcome.requests if "claims" in state_of(request))
    state = state_of(verification_request)
    assert "diff" not in state
    assert state["claims"] == ["both files changed"]
    assert "claim_0" in verification_request[1]
    assert "source_0" in verification_request[1]
    evidence = state["evidence"]
    assert isinstance(evidence, list)
    diff_items = [item["id"] for item in evidence if isinstance(item, dict) and item.get("kind") == "diff"]
    assert diff_items == ["diff_mathutil.py", "diff_limits.py"]


_ESCALATE_REVIEW = {
    "correctness": {"score": 0, "confidence": 0.95},
    "spec_match": {"score": 0, "confidence": 0.95},
    "test_gap": {"score": 2, "confidence": 0.95},
    "blast_radius": {"score": 2, "confidence": 0.95},
    "safe_to_apply": {"noul": 0.1},
}


class DriftProvider(FakeProvider):
    """Answers that drift per call, as a live model can between the requests of one tool call."""

    def __init__(self, answer_sets: list[Mapping[str, Any]]) -> None:
        super().__init__(answer_sets[0])
        self._answer_sets = answer_sets
        self._calls = 0

    @override
    async def _send(
        self, state: JsonValue, questions: dict[str, JsonValue], model: str, timeout: float | None
    ) -> Evaluation:
        answers = self._answer_sets[min(self._calls, len(self._answer_sets) - 1)]
        self._calls += 1
        self.requests.append((state, questions))
        return Evaluation(dict(answers), Usage(1, 1), self.name, model)


async def _call_gate_drift(arguments: Mapping[str, Any], answer_sets: list[Mapping[str, Any]]) -> Outcome:
    provider = DriftProvider(answer_sets)
    toolset = Toolset(Runtime(Settings(), provider_factory=lambda _: provider), TOOLS)
    try:
        result = await toolset.call("jev_gate", arguments)
    finally:
        await toolset.aclose()
    text = cast(TextContent, result.content[0]).text
    return Outcome(json.loads(text), bool(result.is_error), text, provider.requests)


async def test_file_list_gate_payload_rows_agree_with_the_action_under_drift() -> None:
    """J3: the verification rows are canonical and the shown review half explains the headline.

    Under per-call drift the split path once kept only the last file's rows, so a call could say
    `action: escalate` while every row it showed was verified and auto.
    """
    outcome = await _call_gate_drift(
        {
            "request": "fix the parser",
            "diff": [
                {"path": "src/a.py", "patch": "+ a"},
                {"path": "src/b.py", "patch": "+ b"},
            ],
            "claims": ["both files changed"],
            "evidence": [{"id": "log", "text": "2 passed"}],
        },
        [_ESCALATE_REVIEW, _REVIEW_ANSWERS, _GATE_ANSWERS],
    )
    assert not outcome.is_error, outcome.text
    assert outcome.payload["action"] == "escalate"
    assert outcome.payload["review"]["action"] == "escalate"
    assert outcome.payload["review"]["score_file"] == "src/a.py"
    assert outcome.payload["review"]["reviewed_files"] == ["src/a.py", "src/b.py"]
    assert outcome.payload["verification"]["action"] == "auto"
    row = outcome.payload["verification"]["results"][0]
    assert row["verdict"] == "verified"
    assert row["action"] == "auto"


async def test_gate_summary_partitions_and_a_row_stands_only_when_auto() -> None:
    """by_verdict and by_action each sum to the claim count. stands is not a grep of verified."""
    outcome = await call_tool(
        "jev_gate",
        {
            "request": "reject empty input",
            "diff": "+ return 404",
            "claims": ["empty input is rejected"],
            "evidence": [{"id": "log", "text": "test_empty: PASS"}],
        },
        {
            "correctness": {"score": 2, "confidence": 0.95},
            "spec_match": {"score": 2, "confidence": 0.95},
            "test_gap": {"score": 0, "confidence": 0.95},
            "blast_radius": {"score": 0, "confidence": 0.95},
            "safe_to_apply": {"noul": 0.95},
            "claim_0": {
                "choice": "verified",
                "confidence": 0.4,
                "probabilities": {"verified": 0.4, "contradicted": 0.3, "unsupported": 0.3},
            },
            "source_0": {"choice": "log", "probabilities": {"log": 0.5, "diff": 0.4, "tests": 0.05, "none": 0.05}},
        },
    )
    assert not outcome.is_error, outcome.text
    row = outcome.payload["verification"]["results"][0]
    summary = outcome.payload["verification"]["summary"]
    assert row["verdict"] == "verified"
    assert row["action"] == "escalate"
    assert row["stands"] is False
    assert summary["by_verdict"] == {"verified": 1}
    assert summary["by_action"] == {"escalate": 1}
    assert sum(summary["by_verdict"].values()) == 1
    assert sum(summary["by_action"].values()) == 1
