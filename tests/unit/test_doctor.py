"""``jev-judge-mcp doctor`` reads configuration and sends nothing."""

import ast
import hashlib
import json
import os
import sys
import urllib.request
from pathlib import Path

import httpx
import httpx2
import pytest

from jev_judge_mcp.doctor import ALLOW_RULES, main
from jev_judge_mcp.policy.thresholds import (
    DEFAULT_AUTO_ACCEPT,
    DEFAULT_CLASSIFY_AUTO_ACCEPT,
    DEFAULT_COMPOSITE_FLOOR,
    DEFAULT_MINIMUM_MARGIN,
    DEFAULT_REVIEW_AT_CAP,
    DEFAULT_SCREEN_BLOCK_AT,
    DEFAULT_SCREEN_REVIEW_AT,
    EXISTS_ABSENT_BELOW,
    EXISTS_FOUND_AT,
    SCREEN_RELEVANCE_SKIP_BELOW,
    SCREEN_SUBSTANCE_SKIP_BELOW,
)
from jev_judge_mcp.server import doctor_requested, hook_requested, installer_requested
from jev_judge_mcp.server import main as server_main
from tests.support.stdio import server_env

_DOCTOR = Path(__file__).resolve().parents[2] / "src" / "jev_judge_mcp" / "doctor.py"
MARKER = "marker-doctor-8f3c1a9e-do-not-print"
_POLICY = (
    ("DEFAULT_AUTO_ACCEPT", DEFAULT_AUTO_ACCEPT),
    ("DEFAULT_REVIEW_AT_CAP", DEFAULT_REVIEW_AT_CAP),
    ("DEFAULT_COMPOSITE_FLOOR", DEFAULT_COMPOSITE_FLOOR),
    ("DEFAULT_CLASSIFY_AUTO_ACCEPT", DEFAULT_CLASSIFY_AUTO_ACCEPT),
    ("DEFAULT_MINIMUM_MARGIN", DEFAULT_MINIMUM_MARGIN),
    ("DEFAULT_SCREEN_BLOCK_AT", DEFAULT_SCREEN_BLOCK_AT),
    ("DEFAULT_SCREEN_REVIEW_AT", DEFAULT_SCREEN_REVIEW_AT),
    ("SCREEN_SUBSTANCE_SKIP_BELOW", SCREEN_SUBSTANCE_SKIP_BELOW),
    ("SCREEN_RELEVANCE_SKIP_BELOW", SCREEN_RELEVANCE_SKIP_BELOW),
    ("EXISTS_FOUND_AT", EXISTS_FOUND_AT),
    ("EXISTS_ABSENT_BELOW", EXISTS_ABSENT_BELOW),
)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    kept = server_env()
    for key in list(os.environ):
        if key not in kept:
            monkeypatch.delenv(key)
    # No test may resolve a key file the developer's machine happens to carry (ADR-0046).
    monkeypatch.setenv("JEV_MCP_KEY_FILE", "/nonexistent/jev-mcp-key")


def _field(text: str, label: str) -> str:
    prefix = f"{label:<8}: "
    for line in text.splitlines():
        if line.startswith(prefix):
            return line[len(prefix) :]
    raise AssertionError(f"no {label} line in:\n{text}")


def _spy(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Fail the test if any HTTP transport sends."""
    calls: list[str] = []

    def fail(*args: object, **kwargs: object) -> None:
        calls.append("http")
        raise AssertionError("doctor sent an HTTP request")

    monkeypatch.setattr(httpx.Client, "send", fail)
    monkeypatch.setattr(httpx.AsyncClient, "send", fail)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", fail)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", fail)
    monkeypatch.setattr(httpx2.Client, "send", fail)
    monkeypatch.setattr(httpx2.AsyncClient, "send", fail)
    monkeypatch.setattr(httpx2.HTTPTransport, "handle_request", fail)
    monkeypatch.setattr(httpx2.AsyncHTTPTransport, "handle_async_request", fail)
    monkeypatch.setattr(urllib.request, "urlopen", fail)
    return calls


def _assert_marker_absent(out: str, err: str) -> None:
    assert MARKER not in out
    assert MARKER not in err
    assert "[redacted]" not in out
    assert "[redacted]" not in err


def test_doctor_subcommand_is_only_doctor() -> None:
    assert doctor_requested(["jev-judge-mcp"]) is False
    assert doctor_requested(["jev-judge-mcp", "doctor"]) is True
    assert doctor_requested(["jev-judge-mcp", "install"]) is False
    assert doctor_requested(["jev-judge-mcp", "hook"]) is False
    assert installer_requested(["jev-judge-mcp", "doctor"]) is False
    assert hook_requested(["jev-judge-mcp", "doctor"]) is False


def test_extra_arguments_are_usage(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--live"]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "usage" in captured.err
    assert MARKER not in captured.err


def test_marker_key_is_absent_and_no_http_is_sent(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls = _spy(monkeypatch)
    monkeypatch.setenv("TYPESAFE_API_KEY", MARKER)
    monkeypatch.setenv("JEV_API_BASE_URL", f"https://user:{MARKER}@example.invalid/v1")
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", MARKER)
    code = main([])
    captured = capsys.readouterr()
    assert code == 0
    assert calls == []
    _assert_marker_absent(captured.out, captured.err)
    assert _field(captured.out, "provider") == "typesafe"
    assert _field(captured.out, "via") == "auto: TYPESAFE_API_KEY found"
    assert _field(captured.out, "key").split() == ["TYPESAFE_API_KEY", "CLOUDFLARE_ACCOUNT_ID", "JEV_API_BASE_URL"]
    assert "error" not in {line.split(":", 1)[0].strip() for line in captured.out.splitlines()}


def test_openrouter_client_sends_nothing(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    calls = _spy(monkeypatch)
    monkeypatch.setenv("JEV_PROVIDER", "openrouter")
    monkeypatch.setenv("OPENROUTER_API_KEY", f"sk-or-{MARKER}")
    code = main([])
    captured = capsys.readouterr()
    assert code == 0
    assert calls == []
    _assert_marker_absent(captured.out, captured.err)
    assert _field(captured.out, "provider") == "openrouter"
    assert _field(captured.out, "via") == "explicit: openrouter"
    assert _field(captured.out, "key") == "OPENROUTER_API_KEY"


def test_missing_credential_exits_nonzero(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    calls = _spy(monkeypatch)
    code = main([])
    captured = capsys.readouterr()
    assert code == 1
    assert calls == []
    assert _field(captured.out, "provider") == "(unset)"
    assert _field(captured.out, "via") == "unconfigured"
    assert "TYPESAFE_API_KEY" in _field(captured.out, "error")
    assert captured.err == ""


def test_key_file_line_reports_presence_without_the_value(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    calls = _spy(monkeypatch)
    absent = tmp_path / "absent-key"
    monkeypatch.setenv("JEV_MCP_KEY_FILE", str(absent))
    monkeypatch.setenv("TYPESAFE_API_KEY", MARKER)
    assert main([]) == 0
    out = capsys.readouterr().out
    assert _field(out, "key file") == f"absent at {absent} (env wins)"

    present = tmp_path / "key"
    present.write_text("sk-stored-doctor\n", encoding="utf-8")
    monkeypatch.setenv("JEV_MCP_KEY_FILE", str(present))
    assert main([]) == 0
    out = capsys.readouterr().out
    assert _field(out, "key file") == f"present at {present} (env wins)"
    assert "sk-stored-doctor" not in out  # the stored value is never printed
    assert calls == []


def test_policy_line_uses_threshold_constants(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("TYPESAFE_API_KEY", MARKER)
    main([])
    policy = _field(capsys.readouterr().out, "policy")
    for name, value in _POLICY:
        assert f"{name}={value}" in policy


def test_allow_names_are_the_published_tools(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The reference ten plus the extension (ADR-0048): one allow rule per published tool."""
    monkeypatch.setenv("TYPESAFE_API_KEY", MARKER)
    main([])
    assert len(ALLOW_RULES) == 11
    assert _field(capsys.readouterr().out, "allow").split() == list(ALLOW_RULES)


def test_partial_allow_file_lists_the_missing_seven_and_is_not_written(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    home = tmp_path / "home"
    work = tmp_path / "work"
    home.mkdir()
    work.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("TYPESAFE_API_KEY", MARKER)
    monkeypatch.chdir(work)
    allowed = ALLOW_RULES[:3]
    missing = ALLOW_RULES[3:]
    settings = home / ".claude" / "settings.json"
    settings.parent.mkdir()
    settings.write_text(
        json.dumps({"permissions": {"allow": [*allowed, 1, None], "deny": [MARKER]}}),
        encoding="utf-8",
    )
    before = hashlib.sha256(settings.read_bytes()).hexdigest()
    code = main([])
    captured = capsys.readouterr()
    assert code == 0
    assert hashlib.sha256(settings.read_bytes()).hexdigest() == before
    _assert_marker_absent(captured.out, captured.err)
    assert _field(captured.out, "missing").split() == list(missing)
    assert json.loads(_field(captured.out, "snippet")) == {"permissions": {"allow": list(missing)}}
    for path in (
        home / ".claude" / "settings.json",
        work / ".claude" / "settings.json",
        work / ".claude" / "settings.local.json",
    ):
        assert str(path) in _field(captured.out, "checked")
    assert not (work / ".claude").exists()
    assert list((home / ".claude").iterdir()) == [settings]


def test_bare_mcp_jev_covers_every_tool_and_the_file_is_not_written(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("TYPESAFE_API_KEY", MARKER)
    monkeypatch.chdir(tmp_path)
    settings = tmp_path / ".claude" / "settings.local.json"
    settings.parent.mkdir()
    settings.write_text(json.dumps({"permissions": {"allow": ["mcp__jev"]}}), encoding="utf-8")
    before = hashlib.sha256(settings.read_bytes()).hexdigest()
    code = main([])
    captured = capsys.readouterr()
    assert code == 0
    assert hashlib.sha256(settings.read_bytes()).hexdigest() == before
    _assert_marker_absent(captured.out, captured.err)
    assert not any(line.startswith("missing ") for line in captured.out.splitlines())
    assert "snippet" not in captured.out
    assert "mcp__jev" in _field(captured.out, "claude")


def test_broken_settings_json_does_not_crash_or_change_the_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("TYPESAFE_API_KEY", MARKER)
    settings = tmp_path / ".claude" / "settings.json"
    settings.parent.mkdir()
    settings.write_bytes(b"{")
    before = hashlib.sha256(settings.read_bytes()).hexdigest()
    assert main([], home=tmp_path, cwd=tmp_path) == 0
    assert hashlib.sha256(settings.read_bytes()).hexdigest() == before
    assert _field(capsys.readouterr().out, "missing").split() == list(ALLOW_RULES)


def test_server_dispatches_doctor_and_does_not_start(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls = _spy(monkeypatch)
    monkeypatch.setenv("TYPESAFE_API_KEY", MARKER)
    monkeypatch.setattr(sys, "argv", ["jev-judge-mcp", "doctor"])

    def started(*args: object, **kwargs: object) -> None:
        raise AssertionError("doctor was not dispatched")

    monkeypatch.setattr("jev_judge_mcp.server.load_settings", started)
    monkeypatch.setattr("jev_judge_mcp.server.serve", started)
    with pytest.raises(SystemExit) as caught:
        server_main()
    captured = capsys.readouterr()
    assert caught.value.code == 0
    assert calls == []
    _assert_marker_absent(captured.out, captured.err)


def test_doctor_source_has_no_hand_copied_thresholds() -> None:
    tree = ast.parse(_DOCTOR.read_text(encoding="utf-8"))
    floats = [node.value for node in ast.walk(tree) if isinstance(node, ast.Constant) and isinstance(node.value, float)]
    assert floats == []
    called = [
        node.func.attr for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    ]
    assert "evaluate" not in called
    assert "urlopen" not in called
    text = _DOCTOR.read_text(encoding="utf-8")
    assert "httpx" not in text
    assert "--live" not in text
