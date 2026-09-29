"""The risk-proportional example's contract, pinned to real tool output.

The example reads whatever `jev-judge-mcp judge <tool>` prints, and its docstring names
jev_verify, jev_review, and jev_gate — three tools whose payloads carry confidences in three
different shapes: verify's flat `results[]`, review's `scores{rubric}` beside a top-level
`safe_to_apply`, and gate's both nested under `review` and `verification`. Every DecisionResult
here is generated, not hand-written: `judge_main` runs the real toolset against a fake provider.
A payload-shape change in any of the three tools breaks
the per-shape pins below instead of silently defeating the stricter bar — the exact failure the
delta-critique demonstrated when these envelopes were synthetic.
"""

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from jev_judge_mcp.cli import judge_main
from tests.support.jev import FakeProvider

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT / "examples" / "risk_proportional_thresholds.py"

_VERIFY_ARGS = {
    "claims": ["The parser rejects malformed input.", "The tests cover the parser."],
    "evidence": "The parser now returns an error on malformed input, and new tests cover it.",
}
_VERIFY_ANSWERS = {
    "relation_claim0": {
        "choice": "supports",
        "confidence": 0.97,
        "probabilities": {"supports": 0.97, "contradicts": 0.02, "says_nothing": 0.01},
    },
    "relation_claim1": {
        "choice": "supports",
        "confidence": 0.88,
        "probabilities": {"supports": 0.88, "contradicts": 0.06, "says_nothing": 0.06},
    },
}

_REVIEW_ANSWERS = {
    "correctness": {"score": 2, "confidence": 0.97},
    "spec_match": {"score": 2, "confidence": 0.96},
    "test_gap": {"score": 0, "confidence": 0.85},
    "blast_radius": {"score": 0, "confidence": 0.93},
    "safe_to_apply": {"noul": 0.97},
}
_REVIEW_ARGS = {"request": "fix the parser", "diff": "+ return error on malformed input"}

_GATE_ARGS = {
    "request": "Return 404 for unknown users.",
    "diff": "+ return res.status(404)",
    "claims": ["The unknown-user test passes."],
    "evidence": "PASS returns 404 for an unknown id",
}
_GATE_ANSWERS = {
    "correctness": {"score": 2, "confidence": 0.95},
    "spec_match": {"score": 2, "confidence": 0.95},
    "test_gap": {"score": 0, "confidence": 0.95},
    "blast_radius": {"score": 0, "confidence": 0.95},
    "safe_to_apply": {"noul": 0.95},
    "claim_0": {
        "choice": "verified",
        "confidence": 0.82,
        "probabilities": {"verified": 0.82, "contradicted": 0.09, "unsupported": 0.09},
    },
}


def _decision_envelope(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tool: str,
    arguments: dict[str, Any],
    answers: dict[str, Any],
) -> dict[str, Any]:
    """A DecisionResult exactly as `jev-judge-mcp judge <tool>` writes it.

    `judge_main` runs the real toolset against the fake provider, so the wrapper's `action`,
    `unresolved`, and `confidence` are production code.
    """
    del monkeypatch
    capsys.readouterr()
    assert judge_main([tool], text=json.dumps(arguments), provider=FakeProvider(answers)) == 0
    document = json.loads(capsys.readouterr().out)
    assert document["tool"] == tool
    assert document["error"] is None
    return document


def _run(tmp_path: Path, envelope: dict[str, Any], stakes: str) -> tuple[int, str]:
    decision = tmp_path / "decision.json"
    decision.write_text(json.dumps(envelope), encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, str(EXAMPLE), str(decision), "--stakes", stakes],
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.returncode, proc.stdout.strip()


@pytest.mark.parametrize(
    ("tool", "arguments", "answers", "weak_path", "weak_confidence"),
    [
        pytest.param("jev_verify", _VERIFY_ARGS, _VERIFY_ANSWERS, ("results", 1, "confidence"), 0.88, id="verify"),
        pytest.param(
            "jev_review", _REVIEW_ARGS, _REVIEW_ANSWERS, ("scores", "test_gap", "confidence"), 0.85, id="review"
        ),
        pytest.param(
            "jev_gate", _GATE_ARGS, _GATE_ANSWERS, ("verification", "results", 0, "confidence"), 0.82, id="gate"
        ),
    ],
)
def test_weakest_row_binds_on_every_real_payload_shape(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tool: str,
    arguments: dict[str, Any],
    answers: dict[str, Any],
    weak_path: tuple[Any, ...],
    weak_confidence: float,
) -> None:
    envelope = _decision_envelope(monkeypatch, capsys, tool, arguments, answers)
    assert envelope["action"] == "auto", envelope
    assert envelope["unresolved"] is False
    # The shape pin: the weak confidence sits where this tool's payload puts it, or the suite fails.
    node: Any = envelope["payload"]
    for key in weak_path:
        node = node[key]
    assert node == weak_confidence

    code, message = _run(tmp_path, envelope, "destructive")
    assert code == 2, message
    assert f"{weak_confidence:.4f}" in message

    code, message = _run(tmp_path, envelope, "reversible")
    assert code == 0, message


def test_a_clear_gate_envelope_proceeds_on_destructive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    answers = {
        **_GATE_ANSWERS,
        "claim_0": {
            "choice": "verified",
            "confidence": 0.99,
            "probabilities": {"verified": 0.99, "contradicted": 0.005, "unsupported": 0.005},
        },
    }
    envelope = _decision_envelope(monkeypatch, capsys, "jev_gate", _GATE_ARGS, answers)
    code, message = _run(tmp_path, envelope, "destructive")
    assert code == 0, message
    assert "proceed" in message


def test_a_reviewed_verdict_stops_before_any_bar(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A weak verdict is not the green light: the CLI envelope marks every non-auto decision
    unresolved, and the example stops there before any bar is consulted."""
    answers = {
        "relation_claim0": {
            "choice": "supports",
            "confidence": 0.6,
            "probabilities": {"supports": 0.6, "contradicts": 0.2, "says_nothing": 0.2},
        }
    }
    envelope = _decision_envelope(
        monkeypatch, capsys, "jev_verify", {"claims": ["It works."], "evidence": "It works."}, answers
    )
    assert envelope["action"] == "review"
    assert envelope["unresolved"] is True
    code, message = _run(tmp_path, envelope, "reversible")
    assert code == 1, message
    assert "unresolved" in message


def test_a_contradicted_gate_stays_unresolved_and_stops(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    answers = {
        **_GATE_ANSWERS,
        "claim_0": {
            "choice": "contradicted",
            "confidence": 0.97,
            "probabilities": {"verified": 0.02, "contradicted": 0.97, "unsupported": 0.01},
        },
    }
    envelope = _decision_envelope(monkeypatch, capsys, "jev_gate", _GATE_ARGS, answers)
    assert envelope["unresolved"] is True
    code, message = _run(tmp_path, envelope, "destructive")
    assert code == 1, message
    assert "unresolved" in message
