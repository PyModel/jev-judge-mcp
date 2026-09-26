"""The order-sensitivity probe: positional pairing, wire-order proof, and the live guards.

The stability math is owned here (pure, hand-paired); `tests/evals/test_live_typesafe.py` owns the
shared live-runner guards; the live probe run itself is `tests/evals/test_live_order.py`.
"""

from typing import Any, cast

import pytest

from evals.calibration import order
from evals.runners import live
from tests.support.jev import FakeProvider

STABLE_ANSWER = {
    "choice": "supports",
    "probabilities": {"supports": 0.9, "contradicts": 0.06, "says_nothing": 0.04},
    "confidence": 0.9,
}


def test_pairs_each_claims_verdict_across_the_two_orderings() -> None:
    # Reversed rows answer the claims back to front: row 0 is claim 4. One flip on claim 1.
    forward = ["verified", "contradicted", "unsupported", "verified", "verified"]
    reversed_rows = ["verified", "verified", "unsupported", "verified", "verified"]
    rows = order.claim_stability(forward, reversed_rows)
    assert [row.claim for row in rows] == order.CLAIMS
    assert [row.stable for row in rows] == [True, False, True, True, True]
    assert order.stability_rate(rows) == pytest.approx(0.8)


def test_a_missing_or_invalid_verdict_is_never_stable() -> None:
    forward = [None, "verified", "verified", "verified", "verified"]
    reversed_rows = ["verified"] * 5  # forward row 0 was invalid: not stable even when reversed agrees
    rows = order.claim_stability(forward, reversed_rows)
    assert [row.stable for row in rows] == [False, True, True, True, True]
    assert order.stability_rate([]) is None


def test_mismatched_run_lengths_are_an_error_not_a_rate() -> None:
    with pytest.raises(ValueError, match="both runs must answer every claim"):
        order.claim_stability(["verified"], ["verified", "contradicted"])


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
async def test_the_probe_sends_the_batch_reversed_and_pairs_the_verdicts() -> None:
    """The wire proof: the provider sees the same evidence state twice with the question order
    reversed, and the report pairs each claim's verdict by position, not by luck of dict order."""
    from jev_judge_mcp.settings import Settings
    from jev_judge_mcp.tools import TOOLS
    from jev_judge_mcp.tools.base import Runtime
    from jev_judge_mcp.tools.toolset import Toolset

    provider = FakeProvider({f"relation_claim{i}": STABLE_ANSWER for i in range(5)})
    toolset = Toolset(Runtime(Settings(), provider_factory=lambda _: provider), TOOLS)
    try:
        result = await order.probe(toolset)
    finally:
        await toolset.aclose()

    assert len(provider.requests) == 2
    first_state, first_questions = cast("tuple[dict[str, Any], dict[str, Any]]", provider.requests[0])
    second_state, second_questions = cast("tuple[dict[str, Any], dict[str, Any]]", provider.requests[1])
    assert first_state["evidence"] == second_state["evidence"]
    assert [c["text"] for c in first_state["claims"]] == [c["text"] for c in reversed(second_state["claims"])]
    # Claim ids are positional, so both runs key questions claim0..claim4; the reversal is in the
    # mapping: question claim0 asks about CLAIMS[0] in the forward run and CLAIMS[-1] reversed.
    assert list(first_questions) == list(second_questions)
    for index, claim in enumerate(order.CLAIMS):
        assert claim in first_questions[f"relation_claim{index}"]["instructions"]
        assert claim in second_questions[f"relation_claim{len(order.CLAIMS) - 1 - index}"]["instructions"]

    assert result["total"] == len(order.CLAIMS)
    assert [row["claim"] for row in result["claims"]] == order.CLAIMS
    assert result["stable"] == len(order.CLAIMS)
    assert result["rate"] == pytest.approx(1.0)
    assert result["billed_tokens"] == 4  # the fake reports 1 input + 1 output token per call


def test_the_report_sums_only_numeric_usage() -> None:
    payloads: list[dict[str, Any]] = [
        {"model": "m", "usage": {"input_tokens": 10, "output_tokens": 2}, "results": []},
        {"model": "m", "usage": {"input_tokens": "n/a"}, "results": []},
    ]
    assert order.report(payloads)["billed_tokens"] == 12


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
