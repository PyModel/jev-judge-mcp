"""The jev_extract dialect: what it refuses, and why (ADR-0004). Candidate equality with V8 is the
differential test's job (`tests/parity/test_extract_differential.py`)."""

import pytest

from jev_judge_mcp.extract.candidates import normalize_flags
from jev_judge_mcp.extract.dialect import PatternRejected, from_units, to_units, translate

OUTSIDE = "is outside the supported subset"


def refusal(pattern: str, flags: str = "g") -> str:
    with pytest.raises(PatternRejected) as caught:
        translate(pattern, flags)
    return str(caught.value)


@pytest.mark.parametrize(
    ("raw", "normalized"), [("", "g"), ("i", "ig"), ("G1i", "ig"), ("gig", "gig"), ("ggi", "gig"), ("x", "xg")]
)
def test_flags_normalize_like_the_reference(raw: str, normalized: str) -> None:
    assert normalize_flags(raw) == normalized


@pytest.mark.parametrize("flags", ["xg", "iig", "uvg", "gig", "Ag"])
def test_flags_v8_rejects_keep_v8_text(flags: str) -> None:
    assert refusal("a", flags) == f"Invalid flags supplied to RegExp constructor '{flags}'"


@pytest.mark.parametrize("flag", list("dmsuvy"))
def test_flags_outside_the_subset_are_named(flag: str) -> None:
    assert refusal("a", flag + "g") == f"unsupported regular expression: the '{flag}' flag {OUTSIDE}"


@pytest.mark.parametrize(
    ("pattern", "reason"),
    [
        ("(?<n>a)", f"unsupported regular expression: a named group {OUTSIDE}"),
        ("(?<=a*)b", f"unsupported regular expression: variable-length lookbehind {OUTSIDE}"),
        (
            "(?<=(?=a)a)b",
            f"unsupported regular expression: a lookaround inside a lookbehind {OUTSIDE}",
        ),
        (
            "(a)\\1",
            f"unsupported regular expression: a backreference or legacy octal escape {OUTSIDE}",
        ),
        (
            "\\01",
            f"unsupported regular expression: a backreference or legacy octal escape {OUTSIDE}",
        ),
        ("\\k<a>", f"unsupported regular expression: a named backreference {OUTSIDE}"),
        ("\\p{L}", f"unsupported regular expression: a Unicode property escape {OUTSIDE}"),
        ("\\u{1F600}", f"unsupported regular expression: '\\u{{...}}' {OUTSIDE}"),
        ("\\x4", f"unsupported regular expression: an incomplete '\\x' escape {OUTSIDE}"),
        ("\\c1", f"unsupported regular expression: '\\c' without a control letter {OUTSIDE}"),
        ("\\a", f"unsupported regular expression: the escape '\\a' {OUTSIDE}"),
        ("(?=a)*", f"unsupported regular expression: a quantified assertion {OUTSIDE}"),
        (
            "(a*)+",
            f"unsupported regular expression: a repeated group that can match empty {OUTSIDE}",
        ),
        (
            "(a|)?",
            f"unsupported regular expression: a repeated group that can match empty {OUTSIDE}",
        ),
        (
            "[\\d-z]",
            f"unsupported regular expression: a class range with a class escape endpoint {OUTSIDE}",
        ),
        (
            "a{2147483648}",
            f"unsupported regular expression: a quantifier bound above 2147483647 {OUTSIDE}",
        ),
        ("(?/", "invalid regular expression: invalid group"),
        ("(a", "invalid regular expression: unterminated group"),
        ("a)", "invalid regular expression: unmatched ')'"),
        ("[a", "invalid regular expression: unterminated character class"),
        ("[b-a]", "invalid regular expression: range out of order in character class"),
        ("a{2,1}", "invalid regular expression: numbers out of order in {} quantifier"),
        ("*a", "invalid regular expression: nothing to repeat"),
        ("a**", "invalid regular expression: nothing to repeat"),
        ("{2}", "invalid regular expression: nothing to repeat"),
        ("^+", "invalid regular expression: nothing to repeat"),
        ("\\b*", "invalid regular expression: nothing to repeat"),
        ("a\\", "invalid regular expression: \\ at end of pattern"),
    ],
)
def test_pattern_refusals_are_named(pattern: str, reason: str) -> None:
    assert refusal(pattern) == reason


def test_case_insensitive_needs_ascii_patterns() -> None:
    reason = f"unsupported regular expression: the 'i' flag with a non-ASCII character in the pattern {OUTSIDE}"
    assert refusal("é", "ig") == reason
    assert refusal("[a-é]", "ig") == reason
    assert refusal("\\u00e9", "ig") == reason
    translate("é", "g")
    translate("[^a]\\s.", "ig")


def test_deep_nesting_is_refused_not_crashed() -> None:
    assert "nesting" in refusal("(" * 400 + "a" + ")" * 400)


def test_units_round_trip_astral_and_lone_surrogates() -> None:
    text = "a😀\ud83d b\ude00"
    units = to_units(text)
    assert len(units) == 7
    assert from_units(units) == text
    assert from_units(units[1:2]) == "\ud83d"
