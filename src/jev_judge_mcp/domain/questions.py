"""Canonical Choice, Noul, and Score questions, owned here rather than by any provider SDK.

`to_wire()` reproduces the object the reference builds with `@typesafe-ai/sdk` 0.6.0
`choice`/`noul`/`score`: keys `type`, `instructions`, `criteria`, in that order. The reference
never omits Noul criteria, so a Noul without criteria is not representable.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from jev_judge_mcp.domain.json import JsonValue

type Description = JsonValue
"""Text, a JSON object or array, or `None` for an undescribed label (the SDK's `EntryType`)."""


@dataclass(frozen=True, slots=True)
class ChoiceQuestion:
    """Pick one label; every label gets a probability."""

    instructions: Description
    criteria: Mapping[str, Description]
    type: Literal["choice"] = "choice"

    def to_wire(self) -> dict[str, JsonValue]:
        return {"type": self.type, "instructions": self.instructions, "criteria": dict(self.criteria)}


@dataclass(frozen=True, slots=True)
class NoulCriteria:
    """Descriptions of the yes (`true`) and no (`false`) outcomes."""

    true: Description
    false: Description

    def to_wire(self) -> dict[str, JsonValue]:
        return {"true": self.true, "false": self.false}


@dataclass(frozen=True, slots=True)
class NoulQuestion:
    """The probability that a yes/no condition holds; near 0.5 means uncertain, not medium."""

    instructions: Description
    criteria: NoulCriteria
    type: Literal["noul"] = "noul"

    def to_wire(self) -> dict[str, JsonValue]:
        return {"type": self.type, "instructions": self.instructions, "criteria": self.criteria.to_wire()}


@dataclass(frozen=True, slots=True)
class ScoreQuestion:
    """A probability-weighted position on ordered levels, 0-indexed: N levels score 0 to N-1."""

    instructions: Description
    criteria: Sequence[Description]
    type: Literal["score"] = "score"

    def __post_init__(self) -> None:
        # The SDK rejects a score question with fewer than two levels before sending it.
        if len(self.criteria) < 2:
            raise ValueError("Score criteria must list at least two levels.")

    def to_wire(self) -> dict[str, JsonValue]:
        return {"type": self.type, "instructions": self.instructions, "criteria": list(self.criteria)}


type Question = ChoiceQuestion | NoulQuestion | ScoreQuestion


def questions_to_wire(questions: Mapping[str, Question]) -> dict[str, JsonValue]:
    """The request body's `questions` object, in the caller's key order."""
    return {name: question.to_wire() for name, question in questions.items()}
