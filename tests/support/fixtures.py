"""The one loader for the parity fixture corpus (ADR-0015).

This module is the only place that knows the corpus format: the directory walk, the JSON
shape, call identity, and the divergence tags. It knows nothing about providers, respx,
MCP, or tools — the replay engine (`tests.support.replay`) and the parity suites build on
it. JSON is parsed in document order and never re-keyed, so key order survives into every
comparison. Which calls a suite cares about stays in the suite: the loader walks, it does
not project.
"""

import json
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jev_judge_mcp.domain import (
    ChoiceQuestion,
    NoulCriteria,
    NoulQuestion,
    Question,
    ScoreQuestion,
)

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "parity" / "fixtures"


@dataclass(frozen=True, slots=True)
class Fixture:
    """One recorded fixture file: where it came from and its parsed payload."""

    path: Path
    payload: dict[str, Any]

    @property
    def id(self) -> str:
        return str(self.payload["id"])

    @property
    def relpath(self) -> str:
        """The path inside the corpus: the readable fixture name for pytest ids."""
        return self.path.relative_to(FIXTURES_DIR).as_posix()


@dataclass(frozen=True, slots=True)
class FixtureCall:
    """One recorded tool call: `calls[index]` of its source fixture."""

    fixture: Fixture
    index: int
    payload: dict[str, Any]

    @property
    def id(self) -> str:
        """`<fixture id>#<index>` — the key `divergences.py` and failure messages use."""
        return f"{self.fixture.id}#{self.index}"

    @property
    def test_id(self) -> str:
        """`fixture path :: call id` — a byte-parity failure names its fixture."""
        return f"{self.fixture.relpath} :: {self.id}"


def iter_fixtures() -> Iterator[Fixture]:
    """Every fixture in the corpus, in deterministic path order."""
    for path in sorted(FIXTURES_DIR.glob("*/**/*.json")):  # the top-level index.json is not a fixture
        yield Fixture(path, json.loads(path.read_text(encoding="utf-8")))


def iter_calls() -> Iterator[FixtureCall]:
    """Every call of every fixture: fixtures in path order, calls in recorded order."""
    for fixture in iter_fixtures():
        for index, payload in enumerate(fixture.payload["calls"]):
            yield FixtureCall(fixture, index, payload)


def fixture_by_id(fixture_id: str) -> Fixture:
    """The one fixture whose recorded id is `fixture_id` — for a suite that pins a single case.

    A suite must not walk the corpus itself (ADR-0015): naming the fixture keeps the corpus format
    and the divergence tags behind this loader.
    """
    for fixture in iter_fixtures():
        if fixture.id == fixture_id:
            return fixture
    raise KeyError(f"no fixture with id {fixture_id!r}")


def divergences(item: Fixture | FixtureCall) -> tuple[str, ...]:
    """The divergence tags on the item's fixture — a call shares its fixture's tags."""
    fixture = item if isinstance(item, Fixture) else item.fixture
    return tuple(fixture.payload["divergences"])


def tagged(item: Fixture | FixtureCall, adr: str) -> bool:
    """True when the item's fixture carries the divergence tag, e.g. `"ADR-0007"`."""
    return adr in divergences(item)


def question_from_wire(wire: Mapping[str, Any]) -> Question:
    """The domain question a recorded request body's `questions[name]` wire object encodes."""
    match wire["type"]:
        case "choice":
            return ChoiceQuestion(wire["instructions"], wire["criteria"])
        case "noul":
            return NoulQuestion(wire["instructions"], NoulCriteria(wire["criteria"]["true"], wire["criteria"]["false"]))
        case "score":
            return ScoreQuestion(wire["instructions"], wire["criteria"])
        case other:
            raise AssertionError(f"unknown question type {other!r}")
