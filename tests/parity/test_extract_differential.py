# ruff: noqa: RUF001 - the corpus is made of look-alike characters on purpose
"""Differential test: jev_extract candidates against V8 (ADR-0004).

For every pattern the Python subset accepts, the candidate list, truncation flag, and too-long count
must equal what the reference's pipeline produces in Node 24. A pattern V8 rejects must be rejected
here too. The corpus mixes named cases (astral text beside BMP text, unpaired surrogates, `\\s`
against U+00A0 and U+0085, empty matches, ASCII-only case folding) with seeded generated patterns.
"""

import json
import random
import subprocess
from collections.abc import AsyncIterator, Callable
from typing import Any

import pytest

from jev_judge_mcp.extract.candidates import Found, find_candidates, normalize_flags
from jev_judge_mcp.extract.dialect import to_units
from jev_judge_mcp.extract.executor import InProcessRegexExecutor, RegexExecutor
from jev_judge_mcp.extract.worker import ProcessRegexExecutor
from tests.support.node import REPO_ROOT, find_node

ORACLE = REPO_ROOT / "tests/parity/harness/regex_oracle.mjs"
SEED = 20260921
GENERATED = 3_000

DOCUMENTS = [
    "a😀b 😀c ABC-123 ABC-124 abc-125",
    "x\ud83dy \ude00z 😀😀",
    "tab\there nbsp\u0085nel ls ps﻿bom　ideo end",
    "Kelvin K k K ß SS ſ s S é É",
    "line1\nline2\r\nline3\n",
    "price $1,299.00 and $5.50; v1.2.3 v10.20.30 2026-09-21",
    "aaa bbb aaa ccc a_b a-b 12ab ab12 __",
    "",
    "b",
]

PATTERNS = [
    (".{3}", ""),
    (".", ""),
    ("😀+", ""),
    ("[😀]", ""),
    ("\\ud83d", ""),
    ("\\ude00.", ""),
    ("\\s\\S", ""),
    ("\\S+", ""),
    ("\\s+", ""),
    ("[^\\s]+", ""),
    ("\\w+", ""),
    ("\\W+", ""),
    ("\\d+", ""),
    ("\\D{2}", ""),
    ("\\bab", ""),
    ("\\Bb", ""),
    ("^\\w+", ""),
    ("\\w+$", ""),
    ("line\\d$", ""),
    ("k", "i"),
    ("[a-z]+", "i"),
    ("[Z-a]+", "i"),
    ("[^k]", "i"),
    ("s", "i"),
    ("ss", "i"),
    ("a*", ""),
    ("a*|b", ""),
    ("a*?", ""),
    ("(?:ab|a)(?:c|bcd)?", ""),
    ("a{2}", ""),
    ("a{1,}", ""),
    ("a{,2}", ""),
    ("a{2", ""),
    ("x{1}y}", ""),
    ("]", ""),
    ("[]", ""),
    ("[^]", ""),
    ("[]a]", ""),
    ("[a-]", ""),
    ("[-a]", ""),
    ("[\\b]", ""),
    ("\\$[0-9,]+\\.[0-9]{2}", ""),
    ("v\\d+\\.\\d+\\.\\d+", ""),
    ("(?<=\\$)[0-9,]+", ""),
    ("(?<!\\d)\\d{2}(?!\\d)", ""),
    ("(?=a)\\w+", ""),
    ("(^)*a", ""),
    ("\\x41|\\u0062", ""),
    ("\\cJ", ""),
    ("\\0", ""),
    ("\\/", ""),
    ("\\-", ""),
    ("\\é", ""),
    ("(a|)+b", ""),
    ("(?:a|b)*?c", ""),
    ("[\\s\\d]+", ""),
    ("[\\D]", ""),
    ("[\\W]+", ""),
    ("[\\S]+", ""),
    ("(?:)", ""),
    ("", ""),
    ("a**", ""),
    ("(?/", ""),
    ("(", ""),
    (")", ""),
    ("[z-a]", ""),
    ("x{2,1}", ""),
    ("\\", ""),
    ("+a", ""),
    ("{2}", ""),
    ("a{2}{3}", ""),
    ("^*", ""),
]

ATOMS = [
    "a", "b", "k", "K", "1", "-", " ", "😀", "\\u00a0", "\\u0085", ".", "\\d", "\\D", "\\w", "\\W", "\\s",
    "\\S", "[a-c]", "[^a]", "[😀x]", "[\\s-]", "\\n", "\\ud83d", "\\ude00",
]  # fmt: skip
ASSERTIONS = ["^", "$", "\\b", "\\B"]
QUANTIFIERS = ["", "", "", "*", "+", "?", "{1,2}", "{2}", "*?", "+?", "??"]


def _generate(rng: random.Random, depth: int = 0) -> str:
    terms: list[str] = []
    for _ in range(rng.randint(1, 4)):
        roll = rng.random()
        if roll < 0.12:
            terms.append(rng.choice(ASSERTIONS))
            continue
        if roll < 0.25 and depth < 2:
            inner = "|".join(_generate(rng, depth + 1) for _ in range(rng.randint(1, 2)))
            opener = rng.choice(["(", "(?:", "(?=", "(?!"])
            atom = f"{opener}{inner})"
            if opener in ("(?=", "(?!"):
                terms.append(atom)
                continue
        else:
            atom = rng.choice(ATOMS)
        terms.append(atom + rng.choice(QUANTIFIERS if depth < 1 else QUANTIFIERS[:6]))
    return "".join(terms)


def _generated_document(rng: random.Random) -> str:
    alphabet = ["a", "b", "k", "K", "1", "2", "-", " ", "😀", " ", "\u0085", "\n", "K", "x"]
    return "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 24)))


def corpus() -> list[tuple[str, str, str]]:
    cases = [(document, pattern, flags) for pattern, flags in PATTERNS for document in DOCUMENTS]
    rng = random.Random(SEED)  # noqa: S311 - a seeded corpus, not a secret
    cases += [(_generated_document(rng), _generate(rng), rng.choice(["", "", "i"])) for _ in range(GENERATED)]
    return cases


pytestmark = pytest.mark.anyio

type Case = tuple[str, str, str]

EXECUTORS: dict[str, Callable[[], RegexExecutor]] = {
    "in-process": InProcessRegexExecutor,
    "process": lambda: ProcessRegexExecutor(size=1),
}
"""The in-process adapter is the matcher alone; the process pool adds pickle transport, the worker's
job decoding, and its caps as production runs them."""


@pytest.fixture(scope="module")
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(scope="module")
def reference() -> list[tuple[Case, dict[str, Any]]]:
    """Every corpus case beside V8's answer, from one Node run shared by both executors."""
    node = find_node()
    cases = corpus()
    stdin = "".join(
        json.dumps({"document": d, "pattern": p, "flags": normalize_flags(f)}, ensure_ascii=True) + "\n"
        for d, p, f in cases
    )
    proc = subprocess.run([node, str(ORACLE)], input=stdin.encode(), capture_output=True, check=False, timeout=300)
    assert proc.returncode == 0, proc.stderr.decode()
    expected: list[dict[str, Any]] = json.loads(proc.stdout.decode())
    assert len(expected) == len(cases)
    return list(zip(cases, expected, strict=True))


@pytest.fixture(params=list(EXECUTORS))
async def executor(request: pytest.FixtureRequest) -> AsyncIterator[RegexExecutor]:
    adapter = EXECUTORS[request.param]()
    if isinstance(adapter, ProcessRegexExecutor):
        await adapter.warm()  # a cold start would spend the first case's 1 s deadline
    try:
        yield adapter
    finally:
        await adapter.aclose()


async def python_pipeline(executor: RegexExecutor, document: str, pattern: str, flags: str) -> dict[str, Any] | None:
    """Production's candidate pipeline on `executor`; None where the subset rejects."""
    found = await find_candidates(executor, pattern, flags, to_units(document))
    if not isinstance(found, Found):
        assert found.outcome == "rejected", (pattern, found)
        return None
    return {"candidates": found.candidates, "truncated": found.truncated, "tooLong": found.too_long}


async def test_candidates_match_v8(executor: RegexExecutor, reference: list[tuple[Case, dict[str, Any]]]) -> None:
    accepted = 0
    for (document, pattern, flags), expected in reference:
        ours = await python_pipeline(executor, document, pattern, flags)
        case = f"pattern={pattern!r} flags={flags!r} document={document!r}"
        if expected["error"] is not None:
            assert ours is None, f"V8 rejects ({expected['error']}) but Python accepts: {case}"
            continue
        if ours is None:
            continue  # outside the subset: a Sanctioned Divergence (ADR-0004)
        accepted += 1
        assert ours == {key: value for key, value in expected.items() if key != "error"}, case
    assert accepted > len(reference) // 2


@pytest.mark.parametrize(
    ("pattern", "flags"), [("k", "i"), ("[a-z]", "i"), ("\\w", "i"), ("ſ", ""), ("[^é]", "")], ids=str
)
async def test_named_cases_are_accepted(pattern: str, flags: str) -> None:
    assert await python_pipeline(InProcessRegexExecutor(), "x", pattern, flags) is not None
