"""The risk-proportional example's own contract: the weakest row binds, on every payload shape.

The example reads whatever `jev-judge-mcp judge <tool>` prints, and the three tools it names in
its docstring shape their payloads differently: jev_verify's rows sit flat in `results[]`,
jev_review's confidences sit in `scores{rubric}` beside a top-level `safe_to_apply`, and
jev_gate nests both under `review` and `verification`. This suite runs the script as a caller
does (a real interpreter over a decision file) and asserts the stricter bar fires on the weakest
confidence in each shape — the exact failure the critique demonstrated when the example read only
the flat shape.
"""

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT / "examples" / "risk_proportional_thresholds.py"


def _run(tmp_path: Path, envelope: dict[str, object], stakes: str) -> tuple[int, str]:
    decision = tmp_path / "decision.json"
    decision.write_text(json.dumps(envelope), encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, str(EXAMPLE), str(decision), "--stakes", stakes],
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.returncode, proc.stdout.strip()


def _envelope(payload: dict[str, object], *, action: str = "auto") -> dict[str, object]:
    return {
        "schema_version": 1,
        "policy_version": "2",
        "tool": "x",
        "action": action,
        "reason_codes": [],
        "confidence": None,
        "error": None,
        "unresolved": False,
        "payload": payload,
    }


VERIFY_AUTO = _envelope(
    {
        "results": [
            {"id": "c1", "action": "auto", "confidence": 0.97},
            {"id": "c2", "action": "auto", "confidence": 0.88},
        ]
    }
)
REVIEW_AUTO = _envelope(
    {
        "safe_to_apply": 0.97,
        "scores": {
            "correctness": {"level": 4, "confidence": 0.96},
            "test_gap": {"level": 1, "confidence": 0.85},
        },
    }
)
GATE_AUTO = _envelope(
    {
        "review": {
            "safe_to_apply": 0.96,
            "scores": {"correctness": {"level": 4, "confidence": 0.95}},
        },
        "verification": {"results": [{"claim": "tests pass", "confidence": 0.91}]},
    }
)


def test_weakest_row_binds_on_every_payload_shape(tmp_path: Path) -> None:
    for envelope, weakest in ((VERIFY_AUTO, 0.88), (REVIEW_AUTO, 0.85), (GATE_AUTO, 0.91)):
        code, message = _run(tmp_path, envelope, "destructive")
        assert code == 2, message
        assert f"{weakest:.4f}" in message, (envelope["payload"], message)


def test_clear_gate_envelope_proceeds_on_destructive(tmp_path: Path) -> None:
    envelope = _envelope(
        {
            "review": {"safe_to_apply": 0.99, "scores": {"correctness": {"confidence": 0.99}}},
            "verification": {"results": [{"claim": "tests pass", "confidence": 0.98}]},
        }
    )
    code, message = _run(tmp_path, envelope, "destructive")
    assert code == 0, message
    assert "proceed" in message


def test_reversible_step_takes_the_tools_own_bar(tmp_path: Path) -> None:
    code, message = _run(tmp_path, REVIEW_AUTO, "reversible")
    assert code == 0, message
    assert "reversible" in message


def test_non_auto_action_stops_before_any_bar(tmp_path: Path) -> None:
    code, message = _run(tmp_path, _envelope({}, action="review"), "reversible")
    assert code == 1, message
    assert "honor the action 'review'" in message


def test_unresolved_envelope_stops(tmp_path: Path) -> None:
    envelope = {**REVIEW_AUTO, "unresolved": True, "error": {"code": "provider", "message": "x"}}
    code, message = _run(tmp_path, envelope, "destructive")
    assert code == 1, message
    assert "unresolved" in message
