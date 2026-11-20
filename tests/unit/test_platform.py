"""Package entry point routing and the POSIX startup gate (ADR-0032)."""

import sys

import pytest

from jev_judge_mcp.__main__ import main, require_posix


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


@pytest.mark.parametrize("command", ["install", "setup", "doctor", "hook", "calibrate"])
def test_subcommand_routes_to_its_own_usage(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], command: str
) -> None:
    monkeypatch.setattr(sys, "argv", ["jev-judge-mcp", command, "--help"])
    with pytest.raises(SystemExit) as caught:
        main()
    captured = capsys.readouterr()
    if command == "hook":
        assert caught.value.code == 2
        assert "usage: jev-judge-mcp hook gate" in captured.err
    else:
        assert caught.value.code == 0
        assert f"usage: jev-judge-mcp {command}" in captured.out


@pytest.mark.parametrize("args", [[], ["unknown"], ["--unknown", "install"]])
def test_no_subcommand_routes_to_the_server(monkeypatch: pytest.MonkeyPatch, args: list[str]) -> None:
    monkeypatch.setattr(sys, "argv", ["jev-judge-mcp", *args])

    def serve() -> None:
        raise SystemExit(73)

    monkeypatch.setattr("jev_judge_mcp.server.main", serve)
    with pytest.raises(SystemExit) as caught:
        main()
    assert caught.value.code == 73
