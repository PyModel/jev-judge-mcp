"""The order-sensitivity probe: positional pairing, wire-order proof, control, and the guards.

The stability math is owned here (pure, hand-paired); `tests/evals/test_live_typesafe.py` owns the
shared live-runner guards; the live probe run itself is `tests/evals/test_live_order.py`.
"""

from typing import Any, cast

import pytest
from mcp.types import CallToolResult, TextContent

from evals.calibration import order
from evals.runners import live
from tests.support.jev import FakeProvider

STABLE_ANSWER = {
    "choice": "supports",
    "probabilities": {"supports": 0.9, "contradicts": 0.06, "says_nothing": 0.04},
    "confidence": 0.9,
}


def test_pairs_each_claims_verdict_across_the_two_orderings() -> None:
    # Reversed rows answer the claims back to front: row 0 is claim 19. One order flip on claim 7,
    # one control flip on claim 12 — the two failures are independent readings.
    forward = ["verified"] * 20
    reversed_rows = ["verified"] * 20
    reversed_rows[12] = "contradicted"  # answers claim 19-12 = claim 7: an order flip
    control = ["verified"] * 20
    control[12] = "contradicted"  # claim 12 flips on the identical repeat: not deterministic
    rows = order.claim_stability(forward, reversed_rows, control)
    assert [row.claim for row in rows] == order.CLAIMS
    assert [row.order_stable for row in rows].count(False) == 1
    assert rows[7].order_stable is False
    assert [row.control_stable for row in rows].count(False) == 1
    assert rows[12].control_stable is False


def test_a_missing_or_invalid_verdict_is_never_stable() -> None:
    forward = [None] + ["verified"] * 19
    reversed_rows = ["verified"] * 20
    rows = order.claim_stability(forward, reversed_rows, control=forward)  # the historic two-run shape: no control run
    assert rows[0].order_stable is False and rows[0].control_stable is False
    assert all(row.control_stable for row in rows[1:])


def test_mismatched_run_lengths_are_an_error_not_a_rate() -> None:
    with pytest.raises(ValueError, match="both runs must answer every claim"):
        order.claim_stability(["verified"], ["verified", "contradicted"], ["verified"])
    with pytest.raises(ValueError, match="the control run must answer every claim"):
        order.claim_stability(["verified", "verified"], ["verified", "verified"], ["verified"])


def test_verdicts_reads_only_validated_rows() -> None:
    payload = {
        "results": [
            {"claim": "a", "verdict": "verified"},
            {"claim": "b", "verdict": "unknown", "status": "invalid_response"},
            {"claim": "c", "verdict": "contradicted"},
        ]
    }
    assert order.verdicts(payload) == ["verified", None, "contradicted"]
    assert order.verdicts({}) == []


@pytest.mark.anyio
async def test_the_probe_sends_forward_reversed_and_control_and_pairs_the_verdicts() -> None:
    """The wire proof: the provider sees the same evidence three times — claims authored, claims
    reversed, claims authored again — and the report reads order and control stability separately."""
    from jev_judge_mcp.settings import Settings
    from jev_judge_mcp.tools import TOOLS
    from jev_judge_mcp.tools.base import Runtime
    from jev_judge_mcp.tools.toolset import Toolset

    provider = FakeProvider({f"relation_claim{i}": STABLE_ANSWER for i in range(len(order.CLAIMS))})
    toolset = Toolset(Runtime(Settings(), provider_factory=lambda _: provider), TOOLS)
    try:
        result = await order.probe(toolset)
    finally:
        await toolset.aclose()

    assert len(provider.requests) == 3
    first_state, first_questions = cast("tuple[dict[str, Any], dict[str, Any]]", provider.requests[0])
    second_state, second_questions = cast("tuple[dict[str, Any], dict[str, Any]]", provider.requests[1])
    third_state, _ = cast("tuple[dict[str, Any], dict[str, Any]]", provider.requests[2])
    assert first_state["evidence"] == second_state["evidence"] == third_state["evidence"]
    assert [c["text"] for c in first_state["claims"]] == [c["text"] for c in reversed(second_state["claims"])]
    assert [c["text"] for c in first_state["claims"]] == [c["text"] for c in third_state["claims"]]
    # Claim ids are positional, so both runs key questions claim0..claimN; the reversal is in the
    # mapping: question claim0 asks about CLAIMS[0] in the forward runs and CLAIMS[-1] reversed.
    assert list(first_questions) == list(second_questions)
    for index, claim in enumerate(order.CLAIMS):
        assert claim in first_questions[f"relation_claim{index}"]["instructions"]
        assert claim in second_questions[f"relation_claim{len(order.CLAIMS) - 1 - index}"]["instructions"]

    assert result["order"]["total"] == len(order.CLAIMS)
    assert [row["claim"] for row in result["claims"]] == order.CLAIMS
    assert result["order"] == {"stable": len(order.CLAIMS), "total": len(order.CLAIMS), "rate": 1.0}
    assert result["control"]["rate"] == 1.0
    assert result["deterministic"] is True
    assert result["billed_tokens"] == 6  # the fake reports 1 input + 1 output token per call


def test_the_report_sums_only_numeric_usage() -> None:
    one_row = [{"claim": "c", "verdict": "verified"}]
    payloads: list[dict[str, Any]] = [
        {"model": "m", "usage": {"input_tokens": 10, "output_tokens": 2}, "results": one_row},
        {"model": "m", "usage": {"input_tokens": "n/a"}, "results": one_row},
        {"model": "m", "usage": {"input_tokens": 1}, "results": one_row},
    ]
    assert order.report(payloads)["billed_tokens"] == 13
    with pytest.raises(ValueError, match="no comparable claims"):
        order.report([{"results": []}, {"results": []}, {"results": []}])


def test_a_tool_error_mid_probe_refuses_instead_of_raising() -> None:
    """The untested gap the critique named: an error result from the toolset surfaces as a typed
    refusal from main, never a traceback through json.loads."""

    class Erroring:
        async def call(self, name: str, arguments: dict[str, Any]) -> CallToolResult:
            return CallToolResult(is_error=True, content=[TextContent(type="text", text="MCP error -32602: boom")])

    with pytest.raises(order.ProbeFailed, match="returned an error mid-probe"):
        import anyio

        anyio.run(order.probe, Erroring())


def test_the_live_entry_refuses_like_the_live_runner(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    assert order.main([], {}) == 2
    assert "refused: live evals call a paid provider; set JEV_EVAL_LIVE=1" in capsys.readouterr().err
    monkeypatch.setenv("JEV_PROVIDER", "compatible")
    assert order.main([], {live.LIVE_FLAG: "1"}) == 2
    assert "JEV_PROVIDER='compatible'; live evals run only against 'typesafe'" in capsys.readouterr().err


def test_the_probe_pins_its_model() -> None:
    order.require_probe_model(order.PROBE_MODEL)
    with pytest.raises(live.LiveRunRefusedError, match="is not the probe's pinned"):
        order.require_probe_model("jev-latest")
