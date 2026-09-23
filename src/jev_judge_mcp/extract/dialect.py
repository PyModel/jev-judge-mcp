"""The jev_extract regex dialect: an ECMAScript subset, translated to Python and matched in UTF-16 units (ADR-0004).

Both the pattern and the document are read as UTF-16 code units, one Python character per unit
(`to_units`), so `.`, quantifiers, and classes see what V8 sees without the `u` flag. The translation
spells every construct out explicitly, so no Python default leaks in:

- `.` is one unit other than U+000A, U+000D, U+2028, U+2029. `^` and `$` are the ends of the input.
- `\\d`, `\\w`, `\\b` are ASCII. `\\s` is ECMAScript WhiteSpace plus LineTerminator.
- Literals are escaped as `\\uXXXX`, so a JS literal `{` or `]` can never become Python syntax.
- `i` is ASCII-only case folding, and is refused when a literal or class endpoint is not ASCII.

Anything else is refused with a named reason rather than guessed at: the flags `d m s u v y`, named
groups, backreferences, legacy octal escapes, property escapes, `\\u{...}`, lookbehind that is not
fixed-length, quantified assertions, a variable repeat of a group that can match empty (V8 and `re`
treat its empty iterations differently), and alphanumeric identity escapes. Flags V8 itself rejects keep
V8's message, so that text matches the reference.
"""

import re
import sys
from array import array
from dataclasses import dataclass
from typing import NoReturn

V8_FLAGS = "dgimsuvy"
SUBSET_REFUSED_FLAGS = "dmsuvy"
MAX_QUANTIFIER_BOUND = 2**31 - 1

_UNIT_CODEC = "utf-16-le" if sys.byteorder == "little" else "utf-16-be"
_WORD = "A-Za-z0-9_"
_WHITESPACE_UNITS = (
    0x09, 0x0A, 0x0B, 0x0C, 0x0D, 0x20, 0xA0, 0x1680,
    *range(0x2000, 0x200B), 0x2028, 0x2029, 0x202F, 0x205F, 0x3000, 0xFEFF,
)  # fmt: skip
_LINE_TERMINATORS = (0x0A, 0x0D, 0x2028, 0x2029)


class PatternRejected(Exception):
    """The pattern or flags are invalid, or outside the subset. `str()` is the `invalid_pattern` reason."""


@dataclass(frozen=True, slots=True)
class Translated:
    """A Python `re` pattern over unit-space text, and its compile flags."""

    source: str
    flags: int


def to_units(text: str) -> str:
    """One character per UTF-16 code unit, each with the unit's value. Lone surrogates are units too."""
    units = array("H")
    units.frombytes(text.encode(_UNIT_CODEC, "surrogatepass"))
    return "".join(map(chr, units))


def from_units(units: str) -> str:
    """The text a unit-space string stands for: pairs rejoin, a lone surrogate stays one code point."""
    return units.encode(_UNIT_CODEC, "surrogatepass").decode(_UNIT_CODEC, "surrogatepass")


def translate(pattern: str, flags: str) -> Translated:
    """The Python translation of JS `new RegExp(pattern, flags)`, or `PatternRejected`.

    `flags` is already normalized as the reference does: letters only, with one `g`.
    """
    if any(flag not in V8_FLAGS for flag in flags) or len(set(flags)) != len(flags) or {"u", "v"} <= set(flags):
        raise PatternRejected(f"Invalid flags supplied to RegExp constructor '{flags}'")
    for flag in flags:
        if flag in SUBSET_REFUSED_FLAGS:
            _refuse(f"the '{flag}' flag")
    parser = _Parser(to_units(pattern))
    try:
        body = parser.parse()
    except RecursionError:
        _refuse("group nesting this deep")
    ignore_case = "i" in flags
    if ignore_case and parser.non_ascii:
        _refuse("the 'i' flag with a non-ASCII character in the pattern")
    python_flags = re.ASCII | (re.IGNORECASE if ignore_case else 0)
    try:
        re.compile(body.text, python_flags)
    except (re.error, RecursionError, OverflowError) as error:
        _refuse(f"the pattern cannot be matched here ({error})")
    return Translated(body.text, python_flags)


def _refuse(what: str) -> NoReturn:
    raise PatternRejected(f"unsupported regular expression: {what} is outside the supported subset")


def _invalid(what: str) -> NoReturn:
    raise PatternRejected(f"invalid regular expression: {what}")


@dataclass(frozen=True, slots=True)
class _Piece:
    text: str
    min: int
    max: int | None
    """Match length bounds in units; `None` is unbounded."""
    assertion: bool = False
    lookaround: bool = False
    """Contains a lookaround somewhere inside."""


def _unit(value: int) -> str:
    """One unit as Python regex syntax: ASCII letters and digits raw, everything else escaped."""
    char = chr(value)
    return char if char.isascii() and char.isalnum() else f"\\u{value:04x}"


def _ranges(units: tuple[int, ...] | list[int]) -> str:
    return "".join(_unit(u) for u in units)


def _complement(members: set[int]) -> str:
    """Class content matching every unit (0-FFFF) outside `members`, as ranges."""
    out: list[str] = []
    start = 0
    for member in [*sorted(members), 0x10000]:
        if member > start:
            out.append(_unit(start) if member - 1 == start else f"{_unit(start)}-{_unit(member - 1)}")
        start = member + 1
    return "".join(out)


_DIGITS = set(range(0x30, 0x3A))
_WORDS = _DIGITS | set(range(0x41, 0x5B)) | set(range(0x61, 0x7B)) | {0x5F}
_CLASS_ESCAPES = {
    "d": "0-9",
    "w": _WORD,
    "s": _ranges(_WHITESPACE_UNITS),
    "D": _complement(_DIGITS),
    "W": _complement(_WORDS),
    "S": _complement(set(_WHITESPACE_UNITS)),
}
"""Class escapes as class content, so they can stand inside a bracketed class too."""
_DOT = f"[^{_ranges(_LINE_TERMINATORS)}]"
_NOTHING = "[^\\u0000-\\uffff]"
_ANYTHING = "[\\u0000-\\uffff]"
_BOUNDARY = f"(?:(?<=[{_WORD}])(?![{_WORD}])|(?<![{_WORD}])(?=[{_WORD}]))"
_NOT_BOUNDARY = f"(?:(?<=[{_WORD}])(?=[{_WORD}])|(?<![{_WORD}])(?![{_WORD}]))"
_QUANTIFIER = re.compile(r"\{([0-9]+)(,([0-9]*))?\}")
_ASCII_DIGITS = "0123456789"
_CONTROL_ESCAPES = {"f": 0x0C, "n": 0x0A, "r": 0x0D, "t": 0x09, "v": 0x0B}
_HEX = set("0123456789abcdefABCDEF")


class _Parser:
    """Recursive descent over the ECMAScript `Pattern` grammar without `u`, restricted to the subset."""

    def __init__(self, units: str) -> None:
        self.units = units
        self.at = 0
        self.non_ascii = False

    def parse(self) -> _Piece:
        body = self._disjunction()
        if self.at < len(self.units):
            _invalid("unmatched ')'")  # the only way a top-level disjunction stops early
        return body

    def _peek(self, offset: int = 0) -> str | None:
        index = self.at + offset
        return self.units[index] if index < len(self.units) else None

    def _take(self) -> str:
        char = self.units[self.at]
        self.at += 1
        return char

    def _disjunction(self) -> _Piece:
        alternatives = [self._alternative()]
        while self._peek() == "|":
            self.at += 1
            alternatives.append(self._alternative())
        if len(alternatives) == 1:
            return alternatives[0]
        maxima = [a.max for a in alternatives]
        return _Piece(
            "|".join(a.text for a in alternatives),
            min(a.min for a in alternatives),
            None if None in maxima else max(m for m in maxima if m is not None),
            lookaround=any(a.lookaround for a in alternatives),
        )

    def _alternative(self) -> _Piece:
        terms: list[_Piece] = []
        while (char := self._peek()) is not None and char not in "|)":
            terms.append(self._term())
        maxima = [t.max for t in terms]
        return _Piece(
            "".join(t.text for t in terms),
            sum(t.min for t in terms),
            None if None in maxima else sum(m for m in maxima if m is not None),
            lookaround=any(t.lookaround for t in terms),
        )

    def _term(self) -> _Piece:
        atom = self._atom()
        quantifier = self._quantifier()
        if quantifier is None:
            return atom
        low, high, suffix = quantifier
        if atom.assertion and atom.lookaround:
            _refuse("a quantified assertion")
        if atom.assertion:
            _invalid("nothing to repeat")
        if atom.min == 0 and (high is None or high > low):
            # V8 fails an optional iteration that matches empty and backtracks into the body for a
            # longer one; Python's `re` accepts the empty iteration. The two disagree on the match.
            _refuse("a repeated group that can match empty")
        return _Piece(
            f"{atom.text}{suffix}",
            atom.min * low,
            None if high is None or atom.max is None else atom.max * high,
            lookaround=atom.lookaround,
        )

    def _quantifier(self) -> tuple[int, int | None, str] | None:
        char = self._peek()
        if char in ("*", "+", "?"):
            self.at += 1
            low, high = {"*": (0, None), "+": (1, None), "?": (0, 1)}[char]
            text = char
        elif char == "{" and (braced := _QUANTIFIER.match(self.units, self.at)):
            self.at = braced.end()
            low = int(braced.group(1))
            high = low if braced.group(2) is None else (int(braced.group(3)) if braced.group(3) else None)
            if max(low, high or 0) > MAX_QUANTIFIER_BOUND:
                _refuse("a quantifier bound above 2147483647")
            if high is not None and high < low:
                _invalid("numbers out of order in {} quantifier")
            text = f"{{{low}}}" if braced.group(2) is None else f"{{{low},{'' if high is None else high}}}"
        else:
            return None
        if self._peek() == "?":
            self.at += 1
            text += "?"
        return low, high, text

    def _atom(self) -> _Piece:
        char = self._take()
        match char:
            case "^":
                return _Piece("\\A", 0, 0, assertion=True)
            case "$":
                return _Piece("\\Z", 0, 0, assertion=True)
            case ".":
                return _Piece(_DOT, 1, 1)
            case "(":
                return self._group()
            case "[":
                return self._class()
            case "\\":
                return self._atom_escape()
            case "*" | "+" | "?":
                _invalid("nothing to repeat")
            case "{" if _QUANTIFIER.match(self.units, self.at - 1):
                _invalid("nothing to repeat")
            case _:
                return self._literal(ord(char))

    def _literal(self, value: int) -> _Piece:
        if value > 0x7F:
            self.non_ascii = True
        return _Piece(_unit(value), 1, 1)

    def _group(self) -> _Piece:
        if self._peek() != "?":
            body = self._disjunction()
            self._close_group()
            # Captures are never read (only the whole match is), so every group is non-capturing.
            return _Piece(f"(?:{body.text})", body.min, body.max, lookaround=body.lookaround)
        self.at += 1
        kind = self._peek()
        if kind == ":":
            self.at += 1
            body = self._disjunction()
            self._close_group()
            return _Piece(f"(?:{body.text})", body.min, body.max, lookaround=body.lookaround)
        if kind in ("=", "!"):
            self.at += 1
            body = self._disjunction()
            self._close_group()
            return _Piece(f"(?{kind}{body.text})", 0, 0, assertion=True, lookaround=True)
        if kind == "<" and self._peek(1) in ("=", "!"):
            sign = self._peek(1)
            self.at += 2
            body = self._disjunction()
            self._close_group()
            if body.lookaround:
                _refuse("a lookaround inside a lookbehind")
            if body.max != body.min:
                _refuse("variable-length lookbehind")
            return _Piece(f"(?<{sign}{body.text})", 0, 0, assertion=True, lookaround=True)
        if kind == "<":
            _refuse("a named group")
        _invalid("invalid group")

    def _close_group(self) -> None:
        if self._peek() != ")":
            _invalid("unterminated group")
        self.at += 1

    def _atom_escape(self) -> _Piece:
        char = self._peek()
        if char is None:
            _invalid("\\ at end of pattern")
        if char == "b":
            self.at += 1
            return _Piece(_BOUNDARY, 0, 0, assertion=True)
        if char == "B":
            self.at += 1
            return _Piece(_NOT_BOUNDARY, 0, 0, assertion=True)
        if char in _CLASS_ESCAPES:
            self.at += 1
            return _Piece(f"[{_CLASS_ESCAPES[char]}]", 1, 1)
        return self._literal(self._character_escape())

    def _character_escape(self) -> int:
        """The unit a `\\` escape stands for (the `\\` already consumed), shared by atoms and classes."""
        char = self._take()
        if char in _CONTROL_ESCAPES:
            return _CONTROL_ESCAPES[char]
        if char == "c":
            letter = self._peek()
            if letter is None or not (letter.isascii() and letter.isalpha()):
                _refuse("'\\c' without a control letter")
            self.at += 1
            return ord(letter) % 32
        following = self._peek()
        if char == "0" and (following is None or following not in _ASCII_DIGITS):
            return 0
        if char in _ASCII_DIGITS:
            _refuse("a backreference or legacy octal escape")
        if char in ("x", "u"):
            width = 2 if char == "x" else 4
            digits = self.units[self.at : self.at + width]
            if len(digits) == width and set(digits) <= _HEX:
                self.at += width
                return int(digits, 16)
            _refuse("'\\u{...}'" if char == "u" and self._peek() == "{" else f"an incomplete '\\{char}' escape")
        if char in ("k", "p", "P"):
            _refuse("a named backreference" if char == "k" else "a Unicode property escape")
        if char.isascii() and char.isalnum():
            _refuse(f"the escape '\\{char}'")
        return ord(char)

    def _class(self) -> _Piece:
        negate = self._peek() == "^"
        if negate:
            self.at += 1
        members: list[str] = []
        while True:
            char = self._peek()
            if char is None:
                _invalid("unterminated character class")
            if char == "]":
                self.at += 1
                break
            start = self._class_atom()
            if self._peek() == "-" and self._peek(1) not in (None, "]"):
                self.at += 1
                end = self._class_atom()
                if isinstance(start, str) or isinstance(end, str):
                    _refuse("a class range with a class escape endpoint")
                if start > end:
                    _invalid("range out of order in character class")
                members.append(f"{self._endpoint(start)}-{self._endpoint(end)}")
            else:
                members.append(start if isinstance(start, str) else self._endpoint(start))
        if not members:
            return _Piece(_ANYTHING if negate else _NOTHING, 1, 1)
        return _Piece(f"[{'^' if negate else ''}{''.join(members)}]", 1, 1)

    def _endpoint(self, value: int) -> str:
        if value > 0x7F:
            self.non_ascii = True
        return _unit(value)

    def _class_atom(self) -> int | str:
        """A unit, or a class escape's content (`str`)."""
        char = self._take()
        if char != "\\":
            return ord(char)
        escaped = self._peek()
        if escaped is None:
            _invalid("\\ at end of pattern")
        if escaped == "b":
            self.at += 1
            return 0x08
        if escaped == "-":
            self.at += 1
            return ord("-")
        if escaped in _CLASS_ESCAPES:
            self.at += 1
            return _CLASS_ESCAPES[escaped]
        return self._character_escape()
