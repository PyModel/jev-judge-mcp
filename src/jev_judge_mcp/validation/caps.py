"""Applying a cap: truncation, budget errors and their frozen texts (ADR-0014). Values live in
`limits.py`, measurement in `text.py`; the reject behavior is each tool's schema. Every budget here is
a strict greater-than check — the manifest's `>8000 → error` means 8000 is allowed — and every error
string is byte-locked by parity fixtures.

A tool cuts through one `CapLedger` per call, so a cut and its record cannot drift apart.
"""

from dataclasses import dataclass
from typing import Literal

from jev_judge_mcp.serialize import js_number_to_locale_string_en_us
from jev_judge_mcp.text import length, truncate


def exceeds(total: int, cap: int) -> bool:
    """Strictly over budget: the cap itself is allowed (manifest `caps.$comment`)."""
    return total > cap


@dataclass(frozen=True, slots=True)
class CappedText:
    """A text cut to its cap, and whether the cut dropped anything."""

    value: str
    truncated: bool


def cap_text(text: str, cap: int) -> CappedText:
    """`truncate(text, cap)` with its flag: truncated exactly when `text` is strictly over `cap` UTF-16 units."""
    return CappedText(truncate(text, cap), exceeds(length(text), cap))


type CapScope = Literal["context", "item"]
"""Who reads a cut. `context`: a document the judgment is made over (review and gate inputs, gate
claims and evidence, jev_extract's candidate universe), so Policy never returns auto over it.
`item`: a candidate's or class's own text (jev_find, jev_rerank, jev_classify), telemetry only — the
reference returns auto over cut item text."""


class CapLedger:
    """One call's cuts: `text` cuts as `truncate` does and records the scope of every cut it makes."""

    __slots__ = ("_scopes",)

    def __init__(self) -> None:
        self._scopes: set[CapScope] = set()

    def text(self, value: str, cap: int, scope: CapScope) -> str:
        capped = cap_text(value, cap)
        if capped.truncated:
            self._scopes.add(scope)
        return capped.value

    def note(self, scope: CapScope) -> None:
        """Record a cut made elsewhere, such as candidates the regex executor capped or skipped."""
        self._scopes.add(scope)

    @property
    def context_cut(self) -> bool:
        """Whether a document the judgment is made over was cut: the only cut Policy reads."""
        return "context" in self._scopes

    @property
    def scopes(self) -> frozenset[CapScope]:
        """Every scope cut so far, for `ToolResult.truncated`."""
        return frozenset(self._scopes)


def candidate_budget_error(total: int, cap: int, remedy: str) -> str:
    """jev_rerank and jev_extract share this frozen scaffold; only the remedy sentence differs."""
    return f"Batch too large: {total} candidate characters exceeds the {cap} character budget. {remedy}"


def classify_budget_error(items: int, classes: int, cap: int) -> str:
    """jev_classify's item-class pair budget: the cap renders as `toLocaleString('en-US')` digits."""
    return (
        f"Batch too large: {items} items x {classes} classes exceeds the "
        f"{js_number_to_locale_string_en_us(cap)} item-class budget. Split the batch."
    )


def gate_evidence_items_error(cap: int) -> str:
    """jev_gate's item-count budget (`isError` result, not a thrown error)."""
    return f"evidence exceeds {cap} items; split the gate or trim the evidence."


def gate_evidence_aggregate_error(cap: int) -> str:
    """jev_gate's aggregate budget: the cap renders as `toLocaleString('en-US')` digits (ADR-0014)."""
    return (
        f"evidence exceeds the {js_number_to_locale_string_en_us(cap)}-character aggregate budget; "
        "split the gate or trim the evidence."
    )


def gate_diff_aggregate_error(cap: int) -> str:
    """jev_gate's file-list patch budget: worded like jev_review's overflow, rendered the same way."""
    return f"diff exceeds the {js_number_to_locale_string_en_us(cap)}-character aggregate budget"
