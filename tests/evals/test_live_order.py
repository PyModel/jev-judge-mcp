"""Live order-sensitivity probe against the real TypeSafe API (marker `live`).

Deselected by default so `make eval` and `make ci` stay offline; when selected, a missing
TYPESAFE_API_KEY fails instead of skipping. Exactly three provider requests: one jev_verify batch
sent forward, the same batch reversed, and a same-order control (evals/calibration/order.py).
The report carries the billed tokens of those three calls, so the cost is on record with the
result.
"""

import json
import os

import pytest

from evals.calibration import order
from evals.runners import live

pytestmark = pytest.mark.live


@pytest.fixture
def typesafe(monkeypatch: pytest.MonkeyPatch) -> None:
    assert os.environ.get("TYPESAFE_API_KEY"), "live evals need TYPESAFE_API_KEY exported; this is not a skip"
    monkeypatch.setenv("JEV_PROVIDER", "typesafe")
    monkeypatch.setenv("JEV_MCP_MODEL", order.PROBE_MODEL)


@pytest.mark.usefixtures("typesafe")
def test_live_order_probe_reports_per_claim_stability(capsys: pytest.CaptureFixture[str]) -> None:
    assert order.main([], {live.LIVE_FLAG: "1"}) == 0

    report = json.loads(capsys.readouterr().out)
    assert report["model"] == order.PROBE_MODEL
    assert report["order"]["total"] == len(order.CLAIMS)
    assert [row["claim"] for row in report["claims"]] == order.CLAIMS
    # Plumbing, not quality: the rates are whatever the pinned model did, and they must be numbers.
    for block in (report["order"], report["control"]):
        assert block["rate"] is not None and 0.0 <= block["rate"] <= 1.0
    assert isinstance(report["deterministic"], bool)
    assert report["billed_tokens"] > 0, "the three paid calls must report their cost"
