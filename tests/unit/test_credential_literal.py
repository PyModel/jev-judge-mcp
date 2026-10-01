"""The credential-literal detector: ordinary code never hits, known-format literals always do.

A hit denies a Write or Edit outright (ADR-0076), so each direction is pinned as a table at
this module's boundary: the ordinary-code lines are the false positives the detector must
never produce, and the literals are the denials it must always produce.
"""

import pytest

from jev_judge_mcp.credential_literal import has_credential_literal, redact_credential_literals

ORDINARY_CODE = [
    'api_key = os.environ["API_KEY"]',
    "password: str = field(repr=False)",
    "def check_token(token): return token == expected",
    'headers = {"Authorization": f"Bearer {token}"}',
    "secret = settings.secret",
    'author = "Jane Doe <jane@example.com> 2024"',
    'password_help = "Must be 12+ chars, incl. Upper & 1 digit"',
    'token_label = "Your API Token (Step 2)"',
    'secret_key = "CHANGE-ME-IN-PRODUCTION-2024"',
    '<div class="sk-folding-cube-container">',
]

CREDENTIAL_LITERALS = [
    'aws_id = "AKIAABCDEFGHIJKLMNOP"',
    'API_KEY = "Zx9$kq2LmP7vRt4WbN"',
    'token = "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ"',
    'slack = "xoxb-123456789012-abcdefghij"',
    'key = "sk-ant-api03-Xy7kP9mW2qR5tZ8v"',
    "-----BEGIN RSA PRIVATE KEY-----",
    "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dBjftJeZ4CVPmB92K27uhbUJU1p1r_wW1gFWFOEjXk",
    'password = "K3fcnVwZaXpT4mQwLr8s"',
]


@pytest.mark.parametrize("line", ORDINARY_CODE)
def test_ordinary_code_is_never_a_hit(line: str) -> None:
    """A false positive denies a normal write with no provider call, so each line must pass clean."""
    assert has_credential_literal(line) is False
    assert redact_credential_literals(line) == line


@pytest.mark.parametrize("line", CREDENTIAL_LITERALS)
def test_known_format_literals_are_always_a_hit(line: str) -> None:
    assert has_credential_literal(line) is True
    assert "[redacted]" in redact_credential_literals(line)


def test_redaction_replaces_only_the_literal() -> None:
    text = 'token = "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ"  # rotate me\n'
    redacted = redact_credential_literals(text)
    assert redacted == 'token = "[redacted]"  # rotate me\n'
