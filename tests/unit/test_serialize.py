"""`stringify`, `number_to_string`, `to_fixed`, `js_number_to_locale_string_en_us` on hand-checked cases; Node is
the oracle in tests/parity."""

import math

import pytest

from jev_judge_mcp.serialize import (
    UNDEFINED,
    js_key_order,
    js_number_to_locale_string_en_us,
    number_to_string,
    quote,
    stringify,
    to_fixed,
)


@pytest.mark.parametrize(
    ("value", "text"),
    [
        (0.0, "0"),
        (-0.0, "0"),
        (1.0, "1"),
        (-1.5, "-1.5"),
        (0.1 + 0.2, "0.30000000000000004"),
        (1e21, "1e+21"),
        (9.999999999999999e20, "999999999999999900000"),
        (1e-7, "1e-7"),
        (1e-6, "0.000001"),
        (3.2e-5, "0.000032"),
        (5e-324, "5e-324"),
        (1.7976931348623157e308, "1.7976931348623157e+308"),
        (123456789.125, "123456789.125"),
        (1.5e-10, "1.5e-10"),
        (math.nan, "NaN"),
        (math.inf, "Infinity"),
        (-math.inf, "-Infinity"),
    ],
)
def test_number_to_string(value: float, text: str) -> None:
    assert number_to_string(value) == text


@pytest.mark.parametrize(
    ("value", "digits", "text"),
    [
        (0.125, 2, "0.13"),
        (0.375, 2, "0.38"),
        (1.005, 2, "1.00"),
        (0.9, 2, "0.90"),
        (1, 2, "1.00"),
        (-0.0, 2, "0.00"),
        (-0.001, 2, "-0.00"),
        (-0.125, 2, "-0.13"),
        (2.5, 0, "3"),
        (1e21, 2, "1e+21"),
        (-1e21, 2, "-1e+21"),
        (math.nan, 2, "NaN"),
        (-math.inf, 2, "-Infinity"),
    ],
)
def test_to_fixed(value: float, digits: int, text: str) -> None:
    assert to_fixed(value, digits) == text


def test_to_fixed_rejects_out_of_range_digits() -> None:
    with pytest.raises(ValueError, match="between 0 and 100"):
        to_fixed(1.0, 101)


@pytest.mark.parametrize(
    ("value", "text"),
    [
        (0, "0"),
        (-0.0, "-0"),
        (999, "999"),
        (1000, "1,000"),
        (200000, "200,000"),
        (-200000, "-200,000"),
        (1234.5, "1,234.5"),
        (1234.5678, "1,234.568"),
        # V8/ICU round the shortest round-trip digits, half away from zero: 1.0005's double sits
        # below the midpoint, yet the tie rounds up.
        (1.0005, "1.001"),
        (1.0625, "1.063"),
        (-1.0625, "-1.063"),
        (0.0001, "0"),
        (-0.0004, "-0"),
        (0.9995, "1"),
        (999.9995, "1,000"),
        (1e-7, "0"),
        (9007199254740991, "9,007,199,254,740,991"),
        (9007199254740992, "9,007,199,254,740,992"),
        # Unlike toFixed, grouping never switches to Number::toString at 1e21.
        (1e21, "1,000,000,000,000,000,000,000"),
        (-1e21, "-1,000,000,000,000,000,000,000"),
        (1.234e25, "12,340,000,000,000,000,000,000,000"),
        (123456789012345678901, "123,456,789,012,345,680,000"),
        (math.nan, "NaN"),
        (math.inf, "∞"),
        (-math.inf, "-∞"),
    ],
)
def test_js_number_to_locale_string_en_us(value: float, text: str) -> None:
    assert js_number_to_locale_string_en_us(value) == text


def test_stringify_layout() -> None:
    payload: dict[str, object] = {
        "tool": "jev_find",
        "top": [{"id": "a", "probability": 1.0}],
        "empty": {},
        "none": [],
        "status": None,
    }
    assert stringify(payload) == (
        '{\n  "tool": "jev_find",\n  "top": [\n    {\n      "id": "a",\n      "probability": 1\n    }\n  ],\n'
        '  "empty": {},\n  "none": [],\n  "status": null\n}'
    )


def test_stringify_scalars() -> None:
    assert stringify(True) == "true"
    assert stringify(False) == "false"
    assert repr(UNDEFINED) == "UNDEFINED"
    assert stringify(None) == "null"
    assert stringify(math.nan) == "null"
    assert stringify([math.inf, -math.inf]) == "[\n  null,\n  null\n]"
    assert stringify(10**21) == "1e+21"
    assert stringify(10**400) == "null"
    assert stringify(-(10**400)) == "null"
    assert stringify((1, 2)) == "[\n  1,\n  2\n]"


def test_undefined_is_omitted_from_objects_and_null_in_arrays() -> None:
    assert stringify({"a": UNDEFINED, "b": 1}) == '{\n  "b": 1\n}'
    assert stringify({"a": UNDEFINED}) == "{}"
    assert stringify([UNDEFINED]) == "[\n  null\n]"
    with pytest.raises(TypeError):
        stringify(UNDEFINED)


def test_array_index_keys_come_first() -> None:
    assert js_key_order(["b", "10", "a", "2", "01", "-1", "4294967294", "4294967295", "0"]) == [
        "0",
        "2",
        "10",
        "4294967294",
        "b",
        "a",
        "01",
        "-1",
        "4294967295",
    ]
    assert js_key_order(["\u0661"]) == ["\u0661"]  # non-ASCII digits are not indices


@pytest.mark.parametrize(
    ("text", "json"),
    [
        ('a"b', '"a\\"b"'),
        ("back\\slash", '"back\\\\slash"'),
        ("\b\f\n\r\t", '"\\b\\f\\n\\r\\t"'),
        ("\x00\x1f\x7f", '"\\u0000\\u001f\x7f"'),
        ("😀", '"😀"'),
        ("\u2028\u2029", '"\u2028\u2029"'),
        ("\ud83d", '"\\ud83d"'),
        ("\ude00x", '"\\ude00x"'),
        ("\ude00\ud83d", '"\\ude00\\ud83d"'),
        (chr(0xD83D) + chr(0xDE00), '"😀"'),
    ],
)
def test_quote(text: str, json: str) -> None:
    assert quote(text) == json
    quote(text).encode("utf-8")  # always encodable: no raw lone surrogates survive


def test_non_json_values_are_rejected() -> None:
    with pytest.raises(TypeError):
        stringify({"a": object()})
    with pytest.raises(TypeError):
        stringify({1: "a"})
