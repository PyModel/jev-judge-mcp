"""Differential test: `stringify`, `number_to_string`, and `to_fixed` against Node 24 (ADR-0006, ROADMAP P2).

The corpus is seeded, so a failure reproduces. It covers 10,000 generated payloads plus the named
boundaries: `-0.0`, subnormals, the `1e21` and `1e-7` exponent switches, `0.1 + 0.2`-class sums,
array-index keys, undefined members, lone surrogates, and control characters.
"""

import math
import random
import struct

import pytest

from jev_judge_mcp.serialize import UNDEFINED, number_to_string, stringify, to_fixed
from tests.support.node import find_node, run_oracle

SEED = 20260921  # seeded corpus: failures reproduce
PAYLOADS = 10_000

BOUNDARY_FLOATS = [
    0.0,
    -0.0,
    5e-324,
    -5e-324,
    2.2250738585072014e-308,
    2.225073858507201e-308,
    1.7976931348623157e308,
    1e21,
    9.999999999999999e20,
    1.0000000000000001e21,
    1e-7,
    1.0000000000000001e-7,
    9.999999999999999e-8,
    1e-6,
    1e16,
    1e20,
    123456789012345680000.0,
    2**53,
    2**53 + 2,
    0.1 + 0.2,
    0.1 + 0.7,
    0.3 + 0.3 + 0.39,
    0.33 + 0.33 + 0.33,
    1 - 0.99,
    0.125,
    0.375,
    1.005,
    2.675,
    0.000032,
    3.2e-5,
    1.0,
    100.0,
    math.nan,
    math.inf,
    -math.inf,
]
SPECIAL_KEYS = [
    "0",
    "1",
    "10",
    "2",
    "01",
    "-1",
    "1.5",
    "4294967294",
    "4294967295",
    "id",
    "",
    "__proto__",
    "constructor",
]
SPECIAL_CHARS = [
    '"',
    "\\",
    "\n",
    "\r",
    "\t",
    "\b",
    "\f",
    "\x00",
    "\x1f",
    "\x7f",
    "\u2028",
    "\u2029",
    "\ud83d",
    "\ude00",
    "😀",
    "é",
    "\u0301",
    "\u202e",
    "…",
]


def random_float(rng: random.Random) -> float:
    pick = rng.random()
    if pick < 0.25:
        return rng.choice(BOUNDARY_FLOATS)
    if pick < 0.45:
        # Any float64 bit pattern: subnormals, huge exponents, NaN payloads.
        return struct.unpack("<d", rng.getrandbits(64).to_bytes(8, "little"))[0]
    if pick < 0.65:
        return rng.random() * 10 ** rng.randint(-12, 25) * rng.choice((1, -1))
    if pick < 0.85:
        # Probability-like values and their sums.
        return sum(round(rng.random(), rng.randint(1, 4)) for _ in range(rng.randint(1, 5)))
    return float(rng.randint(-(2**60), 2**60))


def random_string(rng: random.Random) -> str:
    chars: list[str] = []
    for _ in range(rng.randint(0, 12)):
        pick = rng.random()
        if pick < 0.4:
            chars.append(rng.choice(SPECIAL_CHARS))
        elif pick < 0.7:
            chars.append(chr(rng.randint(0x20, 0x7E)))
        elif pick < 0.9:
            chars.append(chr(rng.randint(0, 0xFFFF)))
        else:
            chars.append(chr(rng.randint(0x10000, 0x10FFFF)))
    return "".join(chars)


def random_value(rng: random.Random, depth: int) -> object:
    pick = rng.random()
    if depth > 0 and pick < 0.2:
        return {
            (rng.choice(SPECIAL_KEYS) if rng.random() < 0.4 else random_string(rng)): random_value(rng, depth - 1)
            for _ in range(rng.randint(0, 6))
        }
    if depth > 0 and pick < 0.35:
        return [random_value(rng, depth - 1) for _ in range(rng.randint(0, 5))]
    if pick < 0.65:
        return random_float(rng)
    if pick < 0.72:
        return rng.randint(-(2**70), 2**70)
    if pick < 0.87:
        return random_string(rng)
    return rng.choice([None, True, False, UNDEFINED])


def payloads() -> list[object]:
    rng = random.Random(SEED)  # noqa: S311 - reproducible corpus, not crypto
    out: list[object] = [
        {"b": 1, "a": 2, "1": 3, "0": 4},
        {"x": UNDEFINED, "y": [UNDEFINED, 1]},
        {"sum": 0.1 + 0.2, "neg_zero": -0.0, "tiny": 5e-324, "big": 1e21, "small": 1e-7},
        {},
        [],
        [{}],
        {"nested": {"empty": {}, "list": [[]]}},
    ]
    while len(out) < PAYLOADS:
        value = random_value(rng, 3)
        if value is not UNDEFINED:
            out.append(value)
    return out


@pytest.fixture(scope="module")
def node() -> str:
    return find_node()


def test_stringify_matches_node(node: str) -> None:
    corpus = payloads()
    expected = run_oracle(node, [{"op": "stringify", "value": value} for value in corpus])
    mismatches = [(value, want, stringify(value)) for value, want in zip(corpus, expected, strict=True)]
    mismatches = [m for m in mismatches if m[1] != m[2]]
    assert not mismatches[:5], f"{len(mismatches)} of {len(corpus)} differ"


def floats() -> list[float]:
    rng = random.Random(SEED + 1)  # noqa: S311 - reproducible corpus, not crypto
    return BOUNDARY_FLOATS + [random_float(rng) for _ in range(PAYLOADS)]


def test_number_to_string_matches_node(node: str) -> None:
    corpus = floats()
    expected = run_oracle(node, [{"op": "toString", "x": x} for x in corpus])
    mismatches = [(x, want, number_to_string(x)) for x, want in zip(corpus, expected, strict=True)]
    assert not [m for m in mismatches if m[1] != m[2]][:5]


def test_to_fixed_matches_node(node: str) -> None:
    rng = random.Random(SEED + 2)  # noqa: S311 - reproducible corpus, not crypto
    cases = [(x, 2) for x in floats()]
    cases += [(round(rng.random(), 3) + rng.choice((0, 0.0005)), 2) for _ in range(2_000)]
    cases += [(rng.random() * 10 ** rng.randint(-3, 22), rng.randint(0, 20)) for _ in range(2_000)]
    expected = run_oracle(node, [{"op": "toFixed", "x": x, "digits": d} for x, d in cases])
    mismatches = [(x, d, want, to_fixed(x, d)) for (x, d), want in zip(cases, expected, strict=True)]
    assert not [m for m in mismatches if m[2] != m[3]][:5]
