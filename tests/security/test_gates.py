"""The paid live security gate: every test in it is marked `live`, and its cap has its own name. That no
default path runs it is `tests/contract/test_paid_gates.py` (ADR-0027)."""

from tests.support.stdio import REPO_ROOT


def test_every_live_test_is_marked_live() -> None:
    source = (REPO_ROOT / "tests" / "security" / "test_live_typesafe.py").read_text(encoding="utf-8")
    assert "pytestmark = [pytest.mark.live" in source


def test_the_security_cap_has_its_own_name() -> None:
    """eval-live's `LIVE_REQUEST_CAP` (25 tool calls per run) is a different budget from this gate's."""
    source = (REPO_ROOT / "tests" / "security" / "test_live_typesafe.py").read_text(encoding="utf-8")
    assert "SECURITY_REQUEST_CAP = 20" in source
    assert "LIVE_REQUEST_CAP" not in source
