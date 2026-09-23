"""Startup refuses a non-POSIX platform before signals or the extract worker pool (ADR-0032)."""

import sys

import pytest

from jev_judge_mcp.server import main, require_posix


def test_this_platform_is_posix() -> None:
    assert not sys.platform.startswith("win")
    require_posix()


def _must_not_load_settings() -> None:
    pytest.fail("load_settings")


def _must_not_build_server(settings: object) -> None:
    del settings
    pytest.fail("build_server")


def _must_not_stop_on_signal(stop: object) -> None:
    del stop
    pytest.fail("signal")


def test_win32_exits_before_signal_or_worker_setup(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr("jev_judge_mcp.server.load_settings", _must_not_load_settings)
    monkeypatch.setattr("jev_judge_mcp.server.build_server", _must_not_build_server)
    monkeypatch.setattr("jev_judge_mcp.server._stop_on_signal", _must_not_stop_on_signal)

    with pytest.raises(SystemExit) as caught:
        main()

    message = caught.value.code
    assert isinstance(message, str)
    assert message == (
        "unsupported platform win32: jev-judge-mcp is POSIX-only; "
        "Windows is unsupported until Windows-specific behavior is implemented and tested"
    )
