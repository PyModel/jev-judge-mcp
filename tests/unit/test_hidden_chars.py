"""The hidden-character guard: every hiding technique fails, legitimate script and emoji text passes."""

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

TAGS = "".join(chr(0xE0000 + ord(c)) for c in "hi")


@pytest.mark.parametrize(
    ("text", "hit"),
    [
        ("elkaix\u200b\u200c\u2060", "1:7: U+200B"),  # the zero-width watermark main's LICENSE carried
        ("a\nb\u202e", "2:2: U+202E"),  # Trojan Source override, on the second line
        (f"ok{TAGS}", "1:3: U+E0068"),  # ASCII smuggling via tag characters
        ("a\U000e0100", "1:2: U+E0100"),  # variation-selector smuggling
        ("pass\u00adword", "1:5: U+00AD"),
        ("x\ufeff", "1:2: U+FEFF"),
        ("\u0645\u200c\u200c\u062e", "1:2: U+200C"),  # a run of joiners is never shaping
        ("a\x00", "1:2: U+0000"),
    ],
)
def test_hidden_text_fails(text: str, hit: str) -> None:
    assert check.scan([("f", text.encode())])[0] == f"f:{hit}"


@pytest.mark.parametrize(
    "text",
    [
        "\U0001f468\u200d\U0001f469\u200d\U0001f467",  # emoji family
        "\U0001f3f3\ufe0f\u200d\U0001f308",  # rainbow flag
        "\u0645\u06cc\u200c\u062e\u0648\u0627\u0647\u0645",  # Persian ZWNJ
        "\u0915\u094d\u200d\u0937",  # Devanagari half form
        "\u05e9\u05dc\u05d5\u05dd\u200f 123",  # Hebrew with RLM
        "\ufeffleading BOM",
        "\u4f60\u597d \u0645\u0631\u062d\u0628\u0627 \U0001f600",
    ],
)
def test_legitimate_text_passes(text: str) -> None:
    assert check.scan([("f", text.encode())]) == []


@pytest.mark.parametrize("data", ["x\u200b".encode("utf-16"), b"caf\xe9"])
def test_undecodable_file_fails_instead_of_being_skipped(data: bytes) -> None:
    assert "not UTF-8" in check.scan([("f", data)])[0]


def test_outside_a_repository_exits_2(tmp_path: Path) -> None:
    result = subprocess.run([sys.executable, str(SCRIPT)], cwd=tmp_path, capture_output=True, text=True, check=False)
    assert result.returncode == 2, result.stderr
