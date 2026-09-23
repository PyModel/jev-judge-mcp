"""JS-compatible `JSON.stringify(payload, null, 2)` and `Number.prototype.toFixed` (ADR-0006).

Numbers follow ECMAScript Number::toString: shortest round-trip digits, integral values without a
fraction, exponential form only when the decimal exponent is >= 21 or <= -7. Object keys follow JS
own-property order: array-index keys ascending first, then the rest in insertion order.
`js_number_to_locale_string_en_us` (ADR-0014) renders cap-sized integers the way the reference's
error text does.
"""

import math
import re
from collections.abc import Iterable, Mapping
from decimal import ROUND_HALF_UP, Context, Decimal
from typing import Final, final


@final
class _Undefined:
    """JS `undefined`: an object key holding it is omitted, an array slot holding it prints `null`."""

    def __repr__(self) -> str:
        return "UNDEFINED"


UNDEFINED: Final = _Undefined()

_MAX_ARRAY_INDEX = 2**32 - 2
_ESCAPES = {'"': '\\"', "\\": "\\\\", "\b": "\\b", "\f": "\\f", "\n": "\\n", "\r": "\\r", "\t": "\\t"}
_NEEDS_ESCAPE = re.compile('[\ud800-\udbff][\udc00-\udfff]|["\\\\\x00-\x1f\ud800-\udfff]')
_DECIMAL_CONTEXT = Context(prec=400)


def stringify(value: object) -> str:
    """`JSON.stringify(value, null, 2)` for parsed-JSON-shaped values (dict, list, tuple, str, number, bool, None).

    NaN and infinities print `null`, `-0.0` prints `0`, integers print as the float64 JS would hold.
    """
    if value is UNDEFINED:
        raise TypeError("JSON.stringify(undefined) produces no text.")
    return _stringify(value, "")


def stringify_compact(value: object) -> str:
    """`JSON.stringify(value)` with no indentation, for parsed-JSON-shaped values (provider error text)."""
    if value is UNDEFINED:
        raise TypeError("JSON.stringify(undefined) produces no text.")
    return _stringify(value, None)


def _stringify(value: object, indent: str | None) -> str:
    if value is None or value is UNDEFINED:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, int | float):
        number = _to_double(value)
        return number_to_string(number) if math.isfinite(number) else "null"
    if isinstance(value, str):
        return quote(value)
    inner = None if indent is None else indent + "  "
    if isinstance(value, Mapping):
        mapping: Mapping[object, object] = value  # pyright: ignore[reportUnknownVariableType]
        keys = [key for key in mapping if isinstance(key, str)]
        if len(keys) != len(mapping):
            raise TypeError("Object keys must be strings.")
        members = [
            (quote(key), _stringify(mapping[key], inner)) for key in js_key_order(keys) if mapping[key] is not UNDEFINED
        ]
        if not members:
            return "{}"
        if indent is None or inner is None:
            return "{" + ",".join(f"{key}:{item}" for key, item in members) + "}"
        return "{\n" + ",\n".join(f"{inner}{key}: {item}" for key, item in members) + "\n" + indent + "}"
    if isinstance(value, list | tuple):
        items: list[object] | tuple[object, ...] = value  # pyright: ignore[reportUnknownVariableType]
        if not items:
            return "[]"
        if indent is None or inner is None:
            return "[" + ",".join(_stringify(item, None) for item in items) + "]"
        return "[\n" + ",\n".join(inner + _stringify(item, inner) for item in items) + "\n" + indent + "]"
    raise TypeError(f"Cannot serialize {type(value).__name__} as JSON.")


def _to_double(value: int | float) -> float:
    try:
        return float(value)
    except OverflowError:
        return math.inf if value > 0 else -math.inf


def quote(text: str) -> str:
    """JSON.stringify of a string: `"`, `\\`, and C0 controls escaped, lone surrogates as `\\udxxx`, the rest raw."""
    return '"' + _NEEDS_ESCAPE.sub(_escape, text) + '"'


def _escape(match: re.Match[str]) -> str:
    chars = match.group()
    if len(chars) == 2:
        # A surrogate pair held as two code points is one well-formed character in JS.
        return chr(0x10000 + ((ord(chars[0]) - 0xD800) << 10) + (ord(chars[1]) - 0xDC00))
    return _ESCAPES.get(chars) or f"\\u{ord(chars):04x}"


def js_key_order(keys: Iterable[str]) -> list[str]:
    """Keys in JS own-property order: array indices (canonical integers up to 2**32 - 2) ascending, then the rest."""
    indices: list[str] = []
    others: list[str] = []
    for key in keys:
        (indices if _is_array_index(key) else others).append(key)
    indices.sort(key=int)
    return indices + others


def _is_array_index(key: str) -> bool:
    if not key.isascii() or not key.isdigit() or (len(key) > 1 and key[0] == "0"):
        return False
    return int(key) <= _MAX_ARRAY_INDEX


def number_to_string(value: float) -> str:
    """ECMAScript Number::toString(value) for radix 10."""
    if math.isnan(value):
        return "NaN"
    if value == 0:
        return "0"
    if math.isinf(value):
        return "Infinity" if value > 0 else "-Infinity"
    sign = "-" if value < 0 else ""
    # repr gives the shortest digits that round-trip, as Number::toString requires.
    mantissa, _, exponent = repr(abs(value)).partition("e")
    whole, _, fraction = mantissa.partition(".")
    digits = whole + fraction
    point = len(whole) + (int(exponent) if exponent else 0)
    stripped = digits.lstrip("0")
    point -= len(digits) - len(stripped)
    digits = stripped.rstrip("0")
    k, n = len(digits), point
    if k <= n <= 21:
        return sign + digits + "0" * (n - k)
    if 0 < n <= 21:
        return sign + digits[:n] + "." + digits[n:]
    if -6 < n <= 0:
        return sign + "0." + "0" * -n + digits
    e = n - 1
    head = digits[0] + ("." + digits[1:] if k > 1 else "")
    return f"{sign}{head}e{'+' if e >= 0 else '-'}{abs(e)}"


def to_fixed(value: float, digits: int = 2) -> str:
    """`Number.prototype.toFixed(digits)`: round half away from zero on the exact binary value (quirk Q6).

    `0.125` gives `"0.13"` where Python's `f"{0.125:.2f}"` gives `"0.12"`.
    """
    if not 0 <= digits <= 100:
        raise ValueError("toFixed digits must be between 0 and 100.")
    if not math.isfinite(value):
        return number_to_string(value)
    sign = "-" if value < 0 else ""
    magnitude = abs(value)
    if magnitude >= 1e21:
        return sign + number_to_string(magnitude)
    exact = Decimal(magnitude).quantize(Decimal(1).scaleb(-digits), rounding=ROUND_HALF_UP, context=_DECIMAL_CONTEXT)
    return sign + format(exact, "f")


_LOCALE_PLACES: Final = Decimal("0.001")


def js_number_to_locale_string_en_us(value: float) -> str:
    """`Number.prototype.toLocaleString("en-US")` with no options — a frozen JS behavior (ADR-0014).

    Not an i18n API and never process-locale-dependent: the rules are pinned against the parity
    Node (v24.19.0, ICU 78.3), which is where the reference's `200,000` rendering comes from. The
    integer part is grouped in 3s with ","; a non-integer keeps at most 3 fraction digits
    (maximumFractionDigits 3, minimumFractionDigits 0) rounded half away from zero on the shortest
    round-trip digits — `(1.0005).toLocaleString("en-US")` is `"1.001"` although the double sits
    below the midpoint. NaN and the infinities render as ICU does (`"NaN"`, `"∞"`, `"-∞"`). Grouping
    never becomes exponent form (`1e21` renders `"1,000,000,000,000,000,000,000"`, unlike toFixed,
    which switches to Number::toString at 1e21). Negative zero keeps its sign (`"-0"`), and a
    negative magnitude that rounds away keeps it too (`(-0.0004)` → `"-0"`). An int is the double
    JS would hold, as everywhere in this module: 123456789012345678901 groups as
    `"123,456,789,012,345,680,000"`.
    """
    value = _to_double(value)
    if math.isnan(value):
        return "NaN"
    if math.isinf(value):
        return "∞" if value > 0 else "-∞"
    sign = "-" if math.copysign(1.0, value) < 0 else ""
    # V8/ICU round the shortest round-trip decimal (Number::toString digits), not the exact binary
    # value; every form number_to_string emits, "1e+21" included, is a valid Decimal literal.
    rounded = Decimal(number_to_string(abs(value))).quantize(
        _LOCALE_PLACES, rounding=ROUND_HALF_UP, context=_DECIMAL_CONTEXT
    )
    whole, _, fraction = format(rounded, "f").partition(".")
    fraction = fraction.rstrip("0")
    return f"{sign}{int(whole):,}" + (f".{fraction}" if fraction else "")
