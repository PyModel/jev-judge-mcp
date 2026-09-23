"""UTF-16 `length` and `truncate` (ADR-0005).

Every cap in the reference is a JS `.length`, which counts UTF-16 code units. An astral
character (U+10000 and up) is two units there and one code point in Python.
"""

TRUNCATION_MARKER = " […truncated]"

_HIGH_SURROGATES = range(0xD800, 0xDC00)
_LOW_SURROGATES = range(0xDC00, 0xE000)


def length(text: str) -> int:
    """JS `text.length`: the number of UTF-16 code units. Lone surrogates count as one unit."""
    return len(text.encode("utf-16-le", "surrogatepass")) // 2


def truncate(text: str, max_units: int) -> str:
    """JS `truncate` (`lib.ts:47-50`) measured in UTF-16 code units.

    When the cut splits a surrogate pair, the reference keeps the lone high surrogate (quirk Q8);
    this drops it instead, so the result is one unit shorter (ADR-0005). A lone surrogate already
    present in `text` is not a split pair and is kept.
    """
    if length(text) <= max_units:
        return text
    return head(text, max_units) + TRUNCATION_MARKER


def head(text: str, max_units: int) -> str:
    """JS `text.slice(0, max_units)`, except that a split surrogate pair is dropped whole (ADR-0005)."""
    units = 0
    cut = 0
    for index, char in enumerate(text):
        units += 2 if ord(char) > 0xFFFF else 1
        if units > max_units:
            break  # an astral character straddling the cut is the split pair: keep none of it
        cut = index + 1
    result = text[:cut]
    if result and ord(result[-1]) in _HIGH_SURROGATES and cut < len(text) and ord(text[cut]) in _LOW_SURROGATES:
        result = result[:-1]  # a pair held as two code points is split the same way
    return result
