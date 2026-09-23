"""Validated answers. Raw answers are whatever JSON the provider returned; validation turns them into these."""

from dataclasses import dataclass
from typing import Literal

type RawAnswer = object
"""One answer as parsed from the provider envelope, before validation. Anything may arrive."""

type ConfidenceKind = Literal["absent", "malformed", "number"]
"""Absent (missing or JSON null), malformed (present but not a unit interval), or a number (ADR-0043)."""


@dataclass(frozen=True, slots=True)
class ChoiceAnswer:
    """A Choice answer that passed `validate_choice`."""

    choice: str
    probabilities: dict[str, float]
    """Every expected label, in the provider's key order."""
    confidence: float | None
    """Wire number. `None` for absent and malformed. Unknown never satisfies a threshold."""
    confidence_kind: ConfidenceKind = "absent"
    """Which of the three confidence states `confidence` came from. A bare float is a number."""

    def __post_init__(self) -> None:
        # A caller that only has the wire number (extract) cannot say malformed. A float is a number.
        if self.confidence is not None:
            if self.confidence_kind == "malformed":
                raise ValueError("A malformed confidence has no number.")
            if self.confidence_kind == "absent":
                object.__setattr__(self, "confidence_kind", "number")
            return
        if self.confidence_kind == "number":
            raise ValueError("A number confidence needs a value.")


@dataclass(frozen=True, slots=True)
class ScoreAnswer:
    """A Score answer that passed `validate_score`."""

    score: float
    confidence: float | None


@dataclass(frozen=True, slots=True)
class RubricAnswer:
    """A Score answer on a caller-supplied rubric that passed `validate_rubric_answer` (ADR-0048).

    Unlike `ScoreAnswer` the rubric length is known, so the answer also carries the validated
    per-level distribution: keys `"0".."n-1"` in the provider's order, each a finite probability.
    """

    score: float
    probabilities: dict[str, float]
    confidence: float | None
