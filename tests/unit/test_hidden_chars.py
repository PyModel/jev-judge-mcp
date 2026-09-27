"""The hidden-character CI guard fails on zero-width and bidi controls, and passes on this tree.

The allow-list is the only exception. It stays empty while every fixture can spell the
character as an escape; a raw character in a fixture is an explicit entry, not a skip.
"""

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check_hidden_chars.py"
spec = importlib.util.spec_from_file_location("check_hidden_chars", SCRIPT)
assert spec is not None and spec.loader is not None
check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)


def test_allow_list_is_empty() -> None:
    assert check.ALLOW == frozenset()


@pytest.mark.parametrize("char", ["\u200b", "\u200c", "\u2060", "\u202e", "\u2066", "\ufeff"])
def test_zero_width_and_bidi_fail(char: str) -> None:
    prefix = "Copyright (c) 2026 elkaix"
    errors = check.scan([("LICENSE", f"{prefix}{char}\n")])
    assert errors == [f"LICENSE:1:{len(prefix) + 1}: U+{ord(char):04X}"]


def test_allow_list_skips_only_the_named_fixture() -> None:
    text = "fixture\u200b"
    allow = frozenset({"tests/fixtures/needs-raw.txt"})
    assert check.scan([("tests/fixtures/needs-raw.txt", text)], allow=allow) == []
    missed = check.scan([("LICENSE", text)], allow=allow)
    assert missed == [f"LICENSE:1:{len('fixture') + 1}: U+200B"]


def test_ordinary_text_passes() -> None:
    assert check.scan([("README.md", "Copyright (c) 2026 elkaix\n")]) == []


def test_repo_passes() -> None:
    result = subprocess.run([sys.executable, str(SCRIPT)], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    assert result.stdout == "hidden character check passed\n"
