"""Action-pattern redaction: the A3 table, ported from jev-use ``src/redact.ts``.

Markers are fakes. None of them is a live credential.
"""

import ast
import logging
from collections.abc import Iterator
from pathlib import Path

import pytest

from jev_judge_mcp.redact_action import REDACTED, redact_action

_MODULE = Path(__file__).resolve().parents[2] / "src" / "jev_judge_mcp" / "redact_action.py"

# 16, 20, 10, 16, and 30 are the source patterns' minimum lengths.
_SK = "sk-FAKE000000000000"
_GH = "ghp_" + "a" * 20
_GH_PAT = "github_pat_" + "b" * 20
_XOX = "xoxb-" + "c" * 10
_AKIA = "AKIA" + "D" * 16
_AIZA = "AIza" + "e" * 30
_JWT = "eyJabcdefgh.ijklmnop.sigbody"
_URL = "urlpassmarker"
_HEADER = "headermarker"
_FLAG = "flagmarker"
_USER = "userpassmarker"
_ASSIGN = "assignmarker"


class _Capture(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())


@pytest.fixture
def logs() -> Iterator[_Capture]:
    handler = _Capture()
    root = logging.getLogger()
    level = root.level
    root.addHandler(handler)
    root.setLevel(logging.DEBUG)
    try:
        yield handler
    finally:
        root.removeHandler(handler)
        root.setLevel(level)


def _assert_redacted(text: str, expected: str, marker: str, logs: _Capture) -> None:
    redacted = redact_action(text)
    assert redacted == expected
    assert marker not in redacted
    assert marker not in "\n".join(logs.messages)


def test_replacement_token_is_the_source_token() -> None:
    assert REDACTED == "[redacted]"


@pytest.mark.parametrize(
    ("text", "expected", "marker"),
    [
        pytest.param(
            f"psql postgres://app:{_URL}@db.internal:5432/orders -c 'select 1'",
            f"psql postgres://app:{REDACTED}@db.internal:5432/orders -c 'select 1'",
            _URL,
            id="url-password",
        ),
        pytest.param(
            f"curl -H 'Authorization: Bearer {_HEADER}' https://example.test",
            f"curl -H 'Authorization: Bearer {REDACTED}' https://example.test",
            _HEADER,
            id="authorization",
        ),
        pytest.param(
            f"Proxy-Authorization: Basic {_HEADER}",
            f"Proxy-Authorization: Basic {REDACTED}",
            _HEADER,
            id="proxy-authorization",
        ),
        pytest.param(f"Cookie: {_HEADER}", f"Cookie: {REDACTED}", _HEADER, id="cookie"),
        pytest.param(f"Set-Cookie: {_HEADER}", f"Set-Cookie: {REDACTED}", _HEADER, id="set-cookie"),
        pytest.param(f"x-api-key: {_HEADER}", f"x-api-key: {REDACTED}", _HEADER, id="x-api-key"),
        pytest.param(f"api-key: {_HEADER}", f"api-key: {REDACTED}", _HEADER, id="api-key"),
        pytest.param(f"x-auth-token: {_HEADER}", f"x-auth-token: {REDACTED}", _HEADER, id="x-auth-token"),
        pytest.param(
            f"tool --password {_FLAG} --verbose",
            f"tool --password {REDACTED} --verbose",
            _FLAG,
            id="flag-password",
        ),
        pytest.param(f"tool --token={_FLAG}", f"tool --token={REDACTED}", _FLAG, id="flag-token"),
        pytest.param(f"tool --api-key {_FLAG}", f"tool --api-key {REDACTED}", _FLAG, id="flag-api-key"),
        pytest.param(f"tool --secret={_FLAG}", f"tool --secret={REDACTED}", _FLAG, id="flag-secret"),
        pytest.param(
            f"curl -u app:{_USER} https://example.test",
            f"curl -u app:{REDACTED} https://example.test",
            _USER,
            id="user-flag",
        ),
        pytest.param(
            f"curl --user app:{_USER} https://example.test",
            f"curl --user app:{REDACTED} https://example.test",
            _USER,
            id="user-long-flag",
        ),
        pytest.param(f"password={_ASSIGN}", f"password={REDACTED}", _ASSIGN, id="assignment"),
        pytest.param(f"api_key: {_ASSIGN}", f"api_key: {REDACTED}", _ASSIGN, id="assignment-api-key"),
        pytest.param(f"token={_ASSIGN};", f"token={REDACTED};", _ASSIGN, id="assignment-stops-at-semicolon"),
        pytest.param(f"echo {_SK}", f"echo sk-{REDACTED}", _SK, id="sk"),
        pytest.param(f"echo sk-proj-{_SK[3:]}", f"echo sk-proj-{REDACTED}", f"sk-proj-{_SK[3:]}", id="sk-proj"),
        pytest.param(f"echo sk-ant-{_SK[3:]}", f"echo sk-ant-{REDACTED}", f"sk-ant-{_SK[3:]}", id="sk-ant"),
        pytest.param(f"echo sk-live-{_SK[3:]}", f"echo sk-live-{REDACTED}", f"sk-live-{_SK[3:]}", id="sk-live"),
        pytest.param(f"echo sk-test-{_SK[3:]}", f"echo sk-test-{REDACTED}", f"sk-test-{_SK[3:]}", id="sk-test"),
        pytest.param(f"echo {_GH}", f"echo ghp_{REDACTED}", _GH, id="github"),
        pytest.param(f"echo {_GH_PAT}", f"echo github_pat_{REDACTED}", _GH_PAT, id="github-pat"),
        pytest.param(f"echo {_XOX}", f"echo xoxb-{REDACTED}", _XOX, id="slack"),
        pytest.param(f"echo {_AKIA}", f"echo AKIA{REDACTED}", _AKIA, id="aws"),
        pytest.param(f"echo {_AIZA}", f"echo AIza{REDACTED}", _AIZA, id="google"),
        pytest.param(f"echo {_JWT}", f"echo eyJ{REDACTED}", _JWT, id="jwt"),
        pytest.param(
            f"tool --token=$TOKEN --password={_FLAG}",
            f"tool --token=$TOKEN --password={REDACTED}",
            _FLAG,
            id="reference-kept-beside-a-secret",
        ),
    ],
)
def test_redacts_secret_values(text: str, expected: str, marker: str, logs: _Capture) -> None:
    _assert_redacted(text, expected, marker, logs)


@pytest.mark.parametrize(
    "text",
    [
        "curl -H 'Authorization: Bearer $GITHUB_TOKEN' https://example.test",
        "tool --token=$TOKEN",
        "tool --token=${TOKEN}",
        "tool --password=%TOKEN%",
        "tool --secret=$(pass)",
        "psql postgres://app:$DB_PASSWORD@db.internal:5432/orders",
    ],
    ids=["github-token", "dollar-token", "braced", "percent", "command-substitution", "url-reference"],
)
def test_references_stay_intact(text: str, logs: _Capture) -> None:
    assert redact_action(text) == text
    assert logs.messages == []


def test_json_escaped_header_stops_at_the_backslash(logs: _Capture) -> None:
    text = 'curl -H \\"Authorization: Bearer ' + _HEADER + '\\"'
    expected = 'curl -H \\"Authorization: Bearer ' + REDACTED + '\\"'
    redacted = redact_action(text)
    assert redacted == expected
    assert _HEADER not in redacted
    assert redacted.endswith(REDACTED + '\\"')
    assert _HEADER not in "\n".join(logs.messages)


def test_fake_sk_marker_inside_a_command_is_absent_from_the_return_value(logs: _Capture) -> None:
    command = f"curl https://example.test/v1 -H 'Authorization: Bearer {_SK}'"
    redacted = redact_action(command)
    assert _SK not in redacted
    assert redacted == f"curl https://example.test/v1 -H 'Authorization: Bearer {REDACTED}'"
    assert _SK not in "\n".join(logs.messages)


def test_imports_nothing_from_providers_or_tools() -> None:
    tree = ast.parse(_MODULE.read_text(encoding="utf-8"))
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            imported.append(base)
            imported.extend(f"{base}.{alias.name}" for alias in node.names)
    for name in imported:
        assert not name.startswith(("jev_judge_mcp.providers", "jev_judge_mcp.tools"))
