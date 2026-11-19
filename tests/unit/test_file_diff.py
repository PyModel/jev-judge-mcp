"""A file list is not silently cut, and an unreviewed file is not auto."""

import json
from collections.abc import Mapping
from typing import Any, cast, override

import anyio
import pytest
from mcp.types import CallToolResult, TextContent

from jev_judge_mcp.domain import JsonValue, Usage
from jev_judge_mcp.limits import GATE
from jev_judge_mcp.providers import Evaluation, ProviderError
from jev_judge_mcp.providers.base import parse_envelope
from jev_judge_mcp.settings import Settings
from jev_judge_mcp.tools import TOOLS, Runtime, Toolset
from jev_judge_mcp.tools.files import REVIEW_FANOUT, combined
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
    the refusal payload; a client keying on isError saw a gate that decided nothing. Both budget
    refusals carry the `input_too_large` code, byte-for-byte the same on string and file-list
    paths.
    """
    for evidence, expected_error, expected_code in (
        (_OVER_ITEMS, "evidence exceeds 16 items; split the gate or trim the evidence.", "input_too_large"),
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
        assert list_code == string_code == {"code": expected_code}
        assert list_result.structured_content == string_result.structured_content == {"code": expected_code}


def test_combined_usage_preserves_unknown_metering_in_either_position() -> None:
    def reply(tokens: int | None) -> Evaluation:
        body: dict[str, object] = {"answers": {}}
        if tokens is not None:
            body["usage"] = {"input_tokens": tokens, "output_tokens": 0}
        envelope = parse_envelope(body, "compatible")
        return Evaluation({}, envelope.usage, "compatible", "test")

    measured, zero, unknown = reply(1_000_000), reply(0), reply(None)
    total = combined([measured, zero]).usage
    assert total.reported is True
    for group in ([measured, unknown], [unknown, measured], [combined([unknown, zero]), measured]):
        partial = combined(group).usage
        assert partial.input_tokens == total.input_tokens == 1_000_000
        assert partial.output_tokens == total.output_tokens == 0
        assert partial.reported is False


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
    """J4: each fitting file receives review context; claims and source questions are asked once.

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
        assert state["request"] == "fix the parser"
        assert state["evidence"] == [{"id": "log", "text": "2 passed"}]
        peer = (
            {"path": "limits.py", "patch": "+ cap"}
            if state["diff"] == "+ add"
            else {"path": "mathutil.py", "patch": "+ add"}
        )
        assert state["related_files"] == [peer]
        assert state["path"] != peer["path"]
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


async def test_gate_refuses_an_oversized_primary_patch_before_reviewing_other_files() -> None:
    result, provider = await _call_gate_raw(
        [{"path": "ok.py", "patch": "+ ok"}, {"path": "large.py", "patch": "x" * (GATE.doc_units + 1)}],
        [{"id": "log", "text": "1 passed"}],
    )
    assert result.is_error and result.structured_content == {"code": "input_too_large"}
    error = json.loads(cast(TextContent, result.content[0]).text)["error"]
    assert "diff[1].patch" in error and str(GATE.doc_units) in error and "hunk" in error
    assert provider.requests == []


async def test_gate_checks_exact_utf8_budget_including_longest_question_and_duplicated_documents() -> None:
    args = {"request": "r", "diff": "+ 😀", "claims": ["c"], "evidence": "x"}
    sample = await call_tool("jev_gate", args, _GATE_ANSWERS)
    ((state, questions),) = sample.requests

    assert isinstance(state, dict)
    state = {k: v for k, v in state.items() if k not in ("patch_lines", "counterevidence_lines")}
    questions = {k: v for k, v in questions.items() if not k.startswith(("defect_", "counterexample_"))}

    def size(name: str) -> int:
        envelope = {"state": state, "questions": {name: questions[name]}}
        return len(json.dumps(envelope, ensure_ascii=False, separators=(",", ":")).encode())

    remaining = 30_000 - max(map(size, questions))
    assert remaining > 0
    at = await call_tool("jev_gate", {**args, "evidence": "x" * (remaining + 1)}, _GATE_ANSWERS)
    assert at.payload["action"] == "auto" and len(at.requests) == 1
    over = await call_tool("jev_gate", {**args, "evidence": "x" * (remaining + 2)}, _GATE_ANSWERS)
    assert over.is_error and over.code == "input_too_large" and not over.requests
    assert "30001" in over.text and "30000" in over.text and "hunk" in over.text


async def test_gate_preflights_canonical_verification_and_all_expanded_questions() -> None:
    result, provider = await _call_gate_raw(_patches(10, 3500), [{"id": "proof", "text": "ok"}])
    assert result.is_error and "30000" in str(result.content[0]) and not provider.requests
    outcome = await call_tool(
        "jev_gate",
        {
            "request": "r",
            "diff": [{"path": f"{i:02}" + "p" * 57, "patch": "+r"} for i in range(48)],
            "claims": ["c"] * 16,
            "evidence": "proof",
        },
        _GATE_ANSWERS,
    )
    assert outcome.is_error and outcome.code == "input_too_large" and not outcome.requests
    assert "60000" in outcome.text and "claim" in outcome.text


async def test_a_file_list_over_the_patch_budget_refuses_naming_the_diff() -> None:
    """F2/G3: the patch-aggregate refusal names the diff, not the evidence, and keeps its code."""
    result, provider = await _call_gate_raw(
        [
            {"path": "big1.py", "patch": "x" * 150_000},
            {"path": "big2.py", "patch": "x" * 150_000},
        ],
        [{"id": "log", "text": "1 passed"}],
    )
    assert result.is_error
    payload = json.loads(cast(TextContent, result.content[0]).text)
    assert payload["tool"] == "jev_gate" and "diff exceeds the 200,000-character aggregate budget" in payload["error"]
    assert "hunk" in payload["error"]
    assert json.loads(cast(TextContent, result.content[1]).text) == {"code": "input_too_large"}
    assert result.structured_content == {"code": "input_too_large"}
    assert provider.requests == []


_ESCALATE_REVIEW = {
    "defect_kind": {
        "choice": "wrong_result",
        "confidence": 1,
        "probabilities": {"none": 0, "wrong_result": 1, "error_handling": 0, "state_change": 0, "safety_refusal": 0},
    },
    "defect_line": {"choice": "L1", "confidence": 1, "probabilities": {"L1": 1, "none": 0}},
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


async def _call_drift(tool: str, arguments: Mapping[str, Any], answer_sets: list[Mapping[str, Any]]) -> Outcome:
    """Call any tool through the real toolset against answers that drift per provider call."""
    provider = DriftProvider(answer_sets)
    toolset = Toolset(Runtime(Settings(), provider_factory=lambda _: provider), TOOLS)
    try:
        result = await toolset.call(tool, arguments)
    finally:
        await toolset.aclose()
    text = cast(TextContent, result.content[0]).text
    return Outcome(json.loads(text), bool(result.is_error), text, provider.requests)


async def _call_gate_drift(arguments: Mapping[str, Any], answer_sets: list[Mapping[str, Any]]) -> Outcome:
    return await _call_drift("jev_gate", arguments, answer_sets)


async def test_file_list_gate_payload_rows_agree_with_the_action_under_drift() -> None:
    """J3: the verification rows are canonical and the shown review half explains the headline.

    Under per-call drift the split path once kept only the last file's rows, so a call could say
    `action: escalate` while every row it showed was verified and auto. The source answer rests
    the claim on one file's implicit diff item, pinning the sanitized `diff:<path>` attribution.
    """
    verification_answers = {
        **_GATE_ANSWERS,
        "source_0": {
            "choice": "diff_src_a.py",
            "probabilities": {"log": 0.05, "diff_src_a.py": 0.85, "diff_src_b.py": 0.05, "none": 0.05},
        },
    }
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
        [_ESCALATE_REVIEW, _REVIEW_ANSWERS, verification_answers],
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
    assert row["supporting_evidence"] == "diff_src_a.py"
    assert row["excerpt"] == "+ a"


async def test_file_list_review_names_each_files_action_under_drift() -> None:
    """Per-file attribution: a non-auto headline names the file that drove it.

    The review half shows the first file's scores while `action` is the worst of them, so under
    drift a caller once saw escalate next to file 0's passing numbers with no field naming the
    file that escalated. `file_actions` maps every reviewed file to its own action; unreviewed
    files are not in it (they stay in unreviewed_files).
    """
    outcome = await _call_drift(
        "jev_review",
        {
            "request": "fix the parser",
            "diff": [
                {"path": "src/a.py", "patch": "+ a"},
                {"path": "src/b.py", "patch": "+ b"},
                {"path": "src/big.py", "patch": "x" * (GATE.doc_units + 1)},
            ],
        },
        [_ESCALATE_REVIEW, _REVIEW_ANSWERS],
    )
    assert not outcome.is_error, outcome.text
    assert outcome.payload["action"] == "escalate"
    assert outcome.payload["score_file"] == "src/a.py"
    assert outcome.payload["file_actions"] == {"src/a.py": "escalate", "src/b.py": "auto"}
    assert "src/big.py" not in outcome.payload["file_actions"]
    assert outcome.payload["unreviewed_files"] == ["src/big.py"]


async def test_a_clamped_review_keeps_each_files_own_action() -> None:
    """G-A/D1: the unreviewed clamp drives the headline, not any reviewed file.

    Every mapping entry says auto while the action is review — the per-file truth, with
    partial/unreviewed_files naming the cause. Pins that the clamp stays on the headline:
    a mapping softened to match it would hide which files were fine.
    """
    outcome = await call_tool(
        "jev_review",
        {
            "request": "fix the parser",
            "diff": [
                {"path": "src/a.py", "patch": "+ a"},
                {"path": "src/b.py", "patch": "+ b"},
                {"path": "src/big.py", "patch": "x" * (GATE.doc_units + 1)},
            ],
        },
        _REVIEW_ANSWERS,
    )
    assert not outcome.is_error, outcome.text
    assert outcome.payload["action"] == "review"
    assert outcome.payload["partial"] is True
    assert outcome.payload["unreviewed_files"] == ["src/big.py"]
    assert outcome.payload["file_actions"] == {"src/a.py": "auto", "src/b.py": "auto"}


async def test_file_list_gate_names_each_files_action_under_drift() -> None:
    """Per-file attribution in the gate's review half, the same mapping jev_review reports."""
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
    assert outcome.payload["verification"]["action"] == "auto"
    assert outcome.payload["review"]["file_actions"] == {"src/a.py": "escalate", "src/b.py": "auto"}


async def test_a_repeated_path_keeps_its_worst_action() -> None:
    """A duplicated path cannot soften the headline: the mapping keeps the worst of its actions."""
    outcome = await _call_drift(
        "jev_review",
        {
            "request": "fix the parser",
            "diff": [
                {"path": "src/a.py", "patch": "+ first"},
                {"path": "src/a.py", "patch": "+ second"},
            ],
        },
        [_ESCALATE_REVIEW, _REVIEW_ANSWERS],
    )
    assert not outcome.is_error, outcome.text
    assert outcome.payload["action"] == "escalate"
    assert outcome.payload["file_actions"] == {"src/a.py": "escalate"}


async def test_a_repeated_path_keeps_its_worst_action_in_the_gate() -> None:
    """G-B: the gate's nested mapping merges a repeated path to its worst, like jev_review's.

    The worst ask comes second here (jev_review's twin pins it first), so a first-wins merge
    fails this test while a last-wins merge fails the twin — both directions covered as a pair.
    """
    outcome = await _call_gate_drift(
        {
            "request": "fix the parser",
            "diff": [
                {"path": "src/a.py", "patch": "+ first"},
                {"path": "src/a.py", "patch": "+ second"},
            ],
            "claims": ["both files changed"],
            "evidence": [{"id": "log", "text": "2 passed"}],
        },
        [_REVIEW_ANSWERS, _ESCALATE_REVIEW, _GATE_ANSWERS],
    )
    assert not outcome.is_error, outcome.text
    assert outcome.payload["action"] == "escalate"
    assert outcome.payload["verification"]["action"] == "auto"
    assert outcome.payload["review"]["file_actions"] == {"src/a.py": "escalate"}


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
    assert row["action"] == "review"
    assert row["stands"] is False
    assert summary["by_verdict"] == {"verified": 1}
    assert summary["by_action"] == {"review": 1}
    assert sum(summary["by_verdict"].values()) == 1
    assert sum(summary["by_action"].values()) == 1


async def test_a_file_list_over_the_file_cap_is_refused_before_any_request() -> None:
    """One request runs per file, so the count is a budget (ADR-0066 amendment): review raises it
    typed, gate returns the isError shape, and neither asks anything."""
    too_many = [{"path": f"f{index}.py", "patch": "+"} for index in range(GATE.files_max + 1)]
    review = await call_tool("jev_review", {"request": "r", "diff": too_many}, {})
    assert review.is_error and review.code == "input_too_large", review.text
    assert f"{GATE.files_max} files" in review.text
    assert review.requests == []
    gate = await call_tool("jev_gate", {"request": "r", "diff": too_many, "claims": ["done"], "evidence": "proof"}, {})
    assert gate.is_error and gate.code == "input_too_large", gate.text
    assert f"{GATE.files_max} files" in gate.text
    assert gate.requests == []
    at_cap = too_many[: GATE.files_max]
    assert not (await call_tool("jev_review", {"request": "r", "diff": at_cap}, {})).is_error


async def test_the_review_file_list_reports_a_cut_as_its_string_path_does() -> None:
    """A request over the document cap is cut for every file review; the payload says so and the
    telemetry outcome carries the scope, exactly like a string diff."""
    answers = {
        "correctness": {"score": 2, "confidence": 0.95},
        "spec_match": {"score": 2, "confidence": 0.95},
        "test_gap": {"score": 0, "confidence": 0.95},
        "blast_radius": {"score": 0, "confidence": 0.95},
        "safe_to_apply": {"noul": 0.95},
    }
    clean = await call_tool("jev_review", {"request": "fix", "diff": [{"path": "a.py", "patch": "+x"}]}, answers)
    assert not clean.is_error and clean.payload["truncated"] is False
    cut = await call_tool(
        "jev_review",
        {"request": "r" * (GATE.doc_units + 1), "diff": [{"path": "a.py", "patch": "+x"}]},
        answers,
    )
    assert not cut.is_error, cut.text
    assert cut.payload["truncated"] is True
    assert cut.payload["action"] != "auto"


class SlowReviewProvider(FakeProvider):
    """Holds each review call open long enough to overlap; a patch containing `low` answers with a
    low-confidence rubric, and one containing `down` fails as a provider outage would."""

    def __init__(self) -> None:
        super().__init__(_GATE_ANSWERS)
        self.in_flight = 0
        self.max_in_flight = 0
        self.cancelled = 0

    @override
    async def _send(
        self, state: JsonValue, questions: dict[str, JsonValue], model: str, timeout: float | None
    ) -> Evaluation:
        self.requests.append((state, questions))
        diff = str(cast(dict[str, JsonValue], state).get("diff", ""))
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            await anyio.sleep(0.02 if "down" not in diff else 0.005)
            if "down" in diff:
                raise ProviderError("the provider is down")
            answers: dict[str, Any] = dict(_GATE_ANSWERS)
            if "low" in diff:
                answers["correctness"] = {"score": 1, "confidence": 0.2}
            return Evaluation(answers, Usage(1, 1), self.name, model)
        except anyio.get_cancelled_exc_class():
            self.cancelled += 1
            raise
        finally:
            self.in_flight -= 1


async def _call_slow(provider: FakeProvider, files: list[str]) -> CallToolResult:
    arguments = {
        "request": "fix the parser",
        "diff": [{"path": f"src/{index}.py", "patch": patch} for index, patch in enumerate(files)],
        "claims": ["the files changed"],
        "evidence": [{"id": "log", "text": "2 passed"}],
    }
    toolset = Toolset(Runtime(Settings(), provider_factory=lambda _: provider), TOOLS)
    try:
        return await toolset.call("jev_gate", arguments)
    finally:
        await toolset.aclose()


async def test_file_reviews_overlap_and_keep_the_input_order() -> None:
    """Per-file reviews run together, not one after another; each action stays on its own file."""
    provider = SlowReviewProvider()
    result = await _call_slow(provider, ["+ a", "+ low", "+ c", "+ d"])
    payload = json.loads(cast(TextContent, result.content[0]).text)
    assert not result.is_error, payload
    assert provider.max_in_flight == 4
    assert payload["review"]["reviewed_files"] == ["src/0.py", "src/1.py", "src/2.py", "src/3.py"]
    assert payload["review"]["file_actions"]["src/1.py"] == "review"
    assert {payload["review"]["file_actions"][path] for path in ("src/0.py", "src/2.py", "src/3.py")} == {"auto"}
    assert len(provider.requests) == 5  # four reviews, then the claims once


async def test_one_call_never_holds_more_than_the_fan_out_bound() -> None:
    """With the process cap off (the default), one file list still opens at most REVIEW_FANOUT reviews."""
    provider = SlowReviewProvider()
    result = await _call_slow(provider, [f"+ {index}" for index in range(REVIEW_FANOUT + 4)])
    assert not result.is_error
    assert provider.max_in_flight == REVIEW_FANOUT


async def test_the_inflight_cap_bounds_the_file_reviews(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JEV_MCP_MAX_INFLIGHT", "2")
    provider = SlowReviewProvider()
    result = await _call_slow(provider, ["+ a", "+ b", "+ c", "+ d"])
    assert not result.is_error
    assert provider.max_in_flight == 2


async def test_one_failed_file_review_fails_the_call_and_cancels_the_rest() -> None:
    """The call fails as one sequential failure would, and no sibling review stays in flight."""
    provider = SlowReviewProvider()
    result = await _call_slow(provider, ["+ a", "+ down", "+ c"])
    assert result.is_error
    assert "the provider is down" in cast(TextContent, result.content[0]).text
    assert provider.cancelled == 2
    assert provider.in_flight == 0
    assert len(provider.requests) == 3  # the claims are never asked


async def test_gate_explains_the_second_files_confidence_at_the_requested_threshold() -> None:
    outcome = await _call_gate_drift(
        {
            "request": "fix the parser",
            "diff": [{"path": "a", "patch": "+ a"}, {"path": "b", "patch": "+ b"}],
            "claims": ["parser fixed"],
            "evidence": "output",
            "review_at": 0.6,
        },
        [_REVIEW_ANSWERS, {**_REVIEW_ANSWERS, "spec_match": {"score": 1.9, "confidence": 0.59}}, _GATE_ANSWERS],
    )
    assert outcome.payload["action"] == "review"
    assert outcome.payload["review"]["score_file"] == "b"
    assert outcome.payload["review"]["scores"]["spec_match"]["confidence"] == 0.59
    for _, questions in outcome.requests[:2]:
        for question in questions.values():
            assert isinstance(question, dict) and "target file" in str(question["instructions"])
            assert "cross-file" in str(question["instructions"])
    (blocker,) = outcome.payload["blockers"]
    assert (blocker["path"], blocker["field"], blocker["value"], blocker["threshold"]) == (
        "b",
        "spec_match.confidence",
        0.59,
        0.6,
    )
    assert blocker["reviewed_file_index"] == 1
    assert "b" in blocker["next_check"] and "reviewed_files[1]" in blocker["next_check"]
    assert blocker["level_assessment"] == "favorable"
    assert blocker["code"] in outcome.payload["reason_codes"]
    assert blocker["next_check"] in outcome.payload["next_checks"]


def _patches(count: int, units: int) -> list[dict[str, str]]:
    return [{"path": f"src/f{i}.py", "patch": "+" + "x" * (units - 1)} for i in range(count)]


async def test_gate_keeps_the_accepted_decision_across_file_list_sizes() -> None:
    # The gate must preserve accepted decisions inside its budget and refuse an oversized verification before fan-out.
    for count, units in ((2, 2000), (4, 4000), (6, 6000), (10, 2400), (10, 3000), (10, 7000), (20, 2000), (30, 1000)):
        args = {
            "request": "fix the parser",
            "diff": _patches(count, units),
            "tests": "2 passed",
            "claims": ["parser fixed"],
            "evidence": [{"id": "log", "text": "2 passed"}],
        }
        outcome = await call_tool("jev_gate", args, _GATE_ANSWERS)
        body = outcome.payload
        if count * units >= 30_000:
            assert outcome.is_error and outcome.code == "input_too_large" and not outcome.requests, (count, units)
            continue
        assert (body["action"], body["reason_codes"]) == ("auto", ["accepted"]), (count, units)
        assert body["next_checks"] == ["The row stands. Proceed."]
        assert "blockers" not in body
        assert body["truncated"] is False and body["partial"] is False
        if count == 10 and units == 2400:
            assert [row["path"] for row in body["review"]["context_omissions"]] == [f"src/f{i}.py" for i in range(10)]


async def test_review_stands_when_only_supplemental_siblings_do_not_fit() -> None:
    args = {"request": "fix the parser", "diff": _patches(10, 3000), "tests": "2 passed"}
    outcome = await call_tool("jev_review", args, _REVIEW_ANSWERS)
    body = outcome.payload
    assert body["action"] == "auto"
    assert body["truncated"] is False and body["partial"] is False
    assert "blockers" not in body
    first = body["context_omissions"][0]
    assert first["path"] == "src/f0.py"
    assert first["omitted"] == ["reviewed_files[7]", "reviewed_files[8]", "reviewed_files[9]"]


async def test_omitted_supplemental_evidence_is_named_on_an_accepted_gate() -> None:
    args = {
        "request": "fix the parser",
        "diff": _patches(16, 8),
        "claims": ["parser fixed"],
        "evidence": [{"id": "large", "text": "x" * 14_000}],
    }
    outcome = await call_tool("jev_gate", args, _GATE_ANSWERS)
    assert outcome.payload["action"] == "auto"
    assert outcome.payload["reason_codes"] == ["accepted"]
    assert outcome.payload["next_checks"] == ["The row stands. Proceed."]
    assert "blockers" not in outcome.payload
    omissions = outcome.payload["review"]["context_omissions"]
    assert len(omissions) == 16
    omission = omissions[0]
    assert omission["path"] == "src/f0.py"
    assert omission["omitted"] == ["evidence[0]"]
    assert omission["required_units"] > omission["limit_units"] == GATE.aggregate_evidence_units // 16
    file_state, claim_state = outcome.requests[0][0], outcome.requests[-1][0]
    assert isinstance(file_state, dict) and file_state["evidence"] == []
    assert isinstance(claim_state, dict) and isinstance(claim_state["evidence"], list)
    assert {"id": "large", "text": "x" * 14_000} in claim_state["evidence"]


async def test_gate_checks_every_file_before_dispatch_and_preserves_provider_window_guidance() -> None:
    result, provider = await _call_gate_raw(
        [{"path": "tiny", "patch": "+"}, {"path": "second", "patch": "+" * 31_000}],
        [{"id": "log", "text": "ok"}],
    )
    assert result.is_error and "30000" in str(result.content[0]) and not provider.requests
    error = ProviderError("opaque provider error")
    error.status, error.error_type = 400, "max_tokens_exceeded"
    provider = FakeProvider({}, error=error)
    result = await _call_slow(provider, ["+a"])
    assert result.structured_content == {"code": "input_too_large"}
    assert "32k" in str(result.content[0]) and "hunk" in str(result.content[0])
