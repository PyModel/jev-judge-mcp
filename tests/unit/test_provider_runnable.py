"""A provider that cannot answer any call is refused before the server serves (ADR-0050 shape).

The regression: a bare `uvx --from <checkout> jev-judge-mcp` — the hand-registration form without
the `[typesafe]` extra — started, listed tools, served skills, and failed every judgment call
typed `provider` with "install jev-judge-mcp[typesafe]". `ensure_provider_runnable` moves that
refusal to startup, one stderr line, exit 1. Provider *configuration* errors stay per-call
(ADR-0008); another provider selected imports nothing extra.

The in-process tests patch `jev_judge_mcp.server.sdk_importable`, the binding the gate consults —
never `sys.modules`, which only simulates a missing module in a process that never imported it
(and a full single-process suite does). The subprocess test owns the real missing-SDK boundary.
"""

import subprocess
import sys
from pathlib import Path

import pytest

from jev_judge_mcp.providers.typesafe import sdk_importable
from jev_judge_mcp.server import ensure_provider_runnable, provider_not_runnable_message
from jev_judge_mcp.settings import Settings


def test_a_selected_typesafe_provider_without_the_sdk_exits_at_startup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The bare-install shape: the probe reports the SDK missing, and the key selects typesafe."""
    monkeypatch.setenv("TYPESAFE_API_KEY", "typesafe-test-key-0001")
    monkeypatch.setattr("jev_judge_mcp.server.sdk_importable", lambda: False)
    with pytest.raises(SystemExit) as exited:
        ensure_provider_runnable(Settings())
    assert str(exited.value) == provider_not_runnable_message()
    assert "jev-judge-mcp[typesafe]" in str(exited.value)


def test_another_provider_selected_neither_exits_nor_consults_the_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The canary fails the test if the compatible path ever consults the probe: another
    provider selected must not pay for — or depend on — the SDK's presence."""

    def boom() -> bool:
        raise AssertionError("a non-typesafe provider must not consult the SDK probe")

    monkeypatch.setenv("JEV_PROVIDER", "compatible")
    monkeypatch.setenv("JEV_API_KEY", "compatible-test-key-0001")
    monkeypatch.setenv("JEV_API_BASE_URL", "http://127.0.0.1:9/v1/systemone")
    monkeypatch.setattr("jev_judge_mcp.server.sdk_importable", boom)
    ensure_provider_runnable(Settings())


def test_missing_credentials_stay_a_per_call_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """No credentials is a configuration state the tools report typed, not a startup refusal."""
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    monkeypatch.setenv("HOME", "/nonexistent-jev-home")
    ensure_provider_runnable(Settings())


def test_the_server_entry_refuses_before_serving_without_the_extra(tmp_path: Path) -> None:
    """The real boundary: the actual entry point, an actually missing SDK, a set key.

    The shim shadows `typesafe_sdk` with a module whose import raises `ImportError`, which is
    what a bare install produces. Before the gate this process served stdio protocol; now it
    exits 1 with the one-line fix and writes no protocol bytes to stdout."""
    shim = tmp_path / "shim"
    shim.mkdir()
    (shim / "typesafe_sdk.py").write_text('raise ImportError("no typesafe extra in this install")\n', encoding="utf-8")
    env = {
        "PATH": "/usr/bin:/bin",
        "HOME": str(tmp_path),
        "PYTHONPATH": str(shim),
        "TYPESAFE_API_KEY": "typesafe-test-key-0001",
    }
    proc = subprocess.run(
        [sys.executable, "-m", "jev_judge_mcp"],
        input="",
        capture_output=True,
        text=True,
        check=False,
        env=env,
        timeout=60,
        cwd=tmp_path,
    )
    assert proc.returncode == 1, proc.stderr
    assert provider_not_runnable_message() in proc.stderr
    assert proc.stdout == "", "a refused server must not emit protocol bytes"


def test_sdk_importable_is_true_in_a_full_development_install() -> None:
    """The probe's positive arm runs in every full install; the negative arm is covered above."""
    assert sdk_importable()
