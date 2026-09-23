"""Token usage reported by a provider."""

from dataclasses import dataclass

from jev_judge_mcp.domain.json import JsonValue


@dataclass(frozen=True, slots=True)
class Usage:
    """Token counts for one provider request. A provider that reports none yields zeros (`provider.ts:120`)."""

    input_tokens: int | float = 0
    output_tokens: int | float = 0

    def to_wire(self) -> dict[str, JsonValue]:
        return {"input_tokens": self.input_tokens, "output_tokens": self.output_tokens}
