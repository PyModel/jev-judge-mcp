"""UTF-16 `length` and `truncate` (ADR-0005), including the recorded split-pair fixtures."""

import pytest

from jev_judge_mcp.text import TRUNCATION_MARKER, length, truncate
from jev_judge_mcp.validation.caps import CapLedger, CappedText, cap_text
from tests.support.fixtures import fixture_by_id, tagged

HIGH, LOW = chr(0xD83D), chr(0xDE00)  # a surrogate pair held as two code points


@pytest.mark.parametrize(
    ("text", "units"),
    [
        ("", 0),
        ("abc", 3),
        ("é", 1),
        ("e\u0301", 2),
        ("😀", 2),
        ("a😀b", 4),
        ("\ud83d", 1),
        (HIGH + LOW, 2),
        ("\u202e", 1),
    ],
)
def test_length_counts_utf16_units(text: str, units: int) -> None:
    assert length(text) == units


def test_truncate_marks_truncated_text() -> None:
    out = truncate("abcdef", 3)
    assert out == "abc" + TRUNCATION_MARKER
    assert out.endswith("…truncated]")
    assert truncate("abc", 3) == "abc"
    assert TRUNCATION_MARKER == " […truncated]"


@pytest.mark.parametrize(
    ("text", "cap", "expected"),
    [
        ("abc", 3, CappedText("abc", truncated=False)),
        ("abcd", 3, CappedText("abc" + TRUNCATION_MARKER, truncated=True)),
        ("ab😀", 3, CappedText("ab" + TRUNCATION_MARKER, truncated=True)),  # the astral pair straddles the cut
        ("a😀", 3, CappedText("a😀", truncated=False)),
    ],
    ids=["at-cap", "over-cap", "split-pair", "astral-at-cap"],
)
def test_cap_text_returns_the_cut_text_with_its_flag(text: str, cap: int, expected: CappedText) -> None:
    assert cap_text(text, cap) == expected


def test_truncate_counts_astral_as_two_units() -> None:
    assert truncate("😀😀", 4) == "😀😀"
    assert truncate("😀😀😀", 4) == "😀😀" + TRUNCATION_MARKER
    assert truncate("a😀b", 3) == "a😀" + TRUNCATION_MARKER


def test_truncate_drops_a_split_pair() -> None:
    # The reference keeps the lone high surrogate (quirk Q8); Python drops it (ADR-0005).
    assert truncate("ab😀", 3) == "ab" + TRUNCATION_MARKER
    assert truncate("😀", 1) == TRUNCATION_MARKER
    assert truncate("ab" + HIGH + LOW, 3) == "ab" + TRUNCATION_MARKER
    assert truncate("ab" + HIGH + LOW + "c", 4) == "ab" + HIGH + LOW + TRUNCATION_MARKER


def test_truncate_keeps_a_lone_surrogate_already_in_the_text() -> None:
    assert truncate("ab\ud83dcd", 3) == "ab\ud83d" + TRUNCATION_MARKER
    assert truncate("ab\ude00cd", 3) == "ab\ude00" + TRUNCATION_MARKER


def _fixture_text(fixture_id: str, path: list[str | int]) -> tuple[str, str]:
    fixture = fixture_by_id(fixture_id)
    assert tagged(fixture, "ADR-0005")
    call = fixture.payload["calls"][0]
    sent: object = call["exchanges"][0]["request"]["body"]["state"]
    given: object = call["arguments"]
    for step in path:
        sent = sent[step]  # type: ignore[index]  # pyright: ignore[reportUnknownVariableType]
        given = given[step]  # type: ignore[index]  # pyright: ignore[reportUnknownVariableType]
    assert isinstance(sent, str) and isinstance(given, str)
    return given, sent


@pytest.mark.parametrize(
    ("name", "path", "cap"),
    [
        ("unicode-astral/find-truncation-splits-surrogate-pair", ["candidates", 0, "text"], 2000),
        ("unicode-astral/gate-claim-truncation-splits-surrogate", ["claims", 0], 2000),
    ],
)
def test_recorded_split_pair_differs_only_by_the_dropped_high_surrogate(
    name: str, path: list[str | int], cap: int
) -> None:
    given, sent = _fixture_text(name, path)
    reference = sent.removesuffix(TRUNCATION_MARKER)
    assert reference.endswith("\ud83d")
    assert truncate(given, cap) == reference[:-1] + TRUNCATION_MARKER


def test_cap_ledger_records_each_cut_under_its_scope() -> None:
    ledger = CapLedger()
    assert ledger.text("abc", 3, "context") == "abc"
    assert ledger.scopes == frozenset()
    assert not ledger.context_cut
    assert ledger.text("abcd", 3, "item") == "abc" + TRUNCATION_MARKER
    assert ledger.scopes == {"item"}
    assert not ledger.context_cut  # an item cut never reaches Policy
    ledger.note("context")
    assert ledger.scopes == {"context", "item"}
    assert ledger.context_cut
