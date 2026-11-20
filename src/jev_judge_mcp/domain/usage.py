"""Token usage reported by a provider."""

from dataclasses import dataclass

from jev_judge_mcp.domain.json import JsonValue


@dataclass(frozen=True, slots=True)
class Usage:
    """Token counts for one provider request. A provider that reports none yields zeros (`provider.ts:120`)."""

    input_tokens: int | float = 0
    output_tokens: int | float = 0

    # False when any contributing provider call omitted metering. Counts then form a lower bound.
    reported: bool = True

    def to_wire(self, *, include_reported: bool = False) -> dict[str, JsonValue]:
        counts: dict[str, JsonValue] = {"input_tokens": self.input_tokens, "output_tokens": self.output_tokens}
        if include_reported:
            counts["reported"] = self.reported
        return counts
