"""jev_extract's per-field decision (`index.ts:1062-1110`): auto/review/not_found and its Reason Code.

Validation has already accepted the answer (or the field had no candidates to ask about); pattern
failures and invalid answers never reach here. The tool projects the decision into its payload.
"""

from dataclasses import dataclass
from typing import Literal

from jev_judge_mcp.policy.actions import classification_decision

type ExtractStatus = Literal["auto", "review", "not_found"]

type ExtractReasonCode = Literal[
    "no_regex_matches", "matches_too_long", "candidate_limit", "none_matched", "none_matched_ambiguous"
]

EXTRACT_REASON_CODES: tuple[ExtractReasonCode, ...] = (
    "no_regex_matches",
    "matches_too_long",
    "candidate_limit",
    "none_matched",
    "none_matched_ambiguous",
)


@dataclass(frozen=True, slots=True)
class ExtractJudgment:
    """Jev's validated pick for a field: a candidate, or none of them, with its top probability and gap."""

    none_matched: bool
    top_probability: float
    gap: float


@dataclass(frozen=True, slots=True)
class ExtractFieldEvidence:
    """One field's candidate universe and, when it had candidates, the judgment over them.

    The universe is incomplete when candidates were capped (`truncated`) or matches were skipped as
    too long (`too_long > 0`): the right value may be among the matches never sent. `truncated` implies
    candidates, so a field without any is judged on `too_long` alone, as the reference does.
    """

    too_long: int
    truncated: bool
    judgment: ExtractJudgment | None
    """`None` iff the field had no candidates, so nothing was asked."""


@dataclass(frozen=True, slots=True)
class ExtractFieldDecision:
    status: ExtractStatus
    reason: ExtractReasonCode | None


def decide_extract_field(evidence: ExtractFieldEvidence, *, threshold: float, margin: float) -> ExtractFieldDecision:
    """Gate the pick on top probability >= threshold and gap >= margin; an incomplete universe is never
    `auto` and never a definite `not_found`, and a negative answer is gated like a positive one."""
    judgment = evidence.judgment
    if judgment is None:
        if evidence.too_long > 0:
            return ExtractFieldDecision("review", "matches_too_long")
        if evidence.truncated:
            # An executor may cap a universe that kept no candidate; "incomplete" outranks "empty".
            return ExtractFieldDecision("review", "candidate_limit")
        return ExtractFieldDecision("not_found", "no_regex_matches")
    incomplete = evidence.truncated or evidence.too_long > 0
    if incomplete:
        return ExtractFieldDecision("review", "candidate_limit")
    decision = classification_decision(judgment.top_probability, judgment.gap, threshold, margin)
    if judgment.none_matched:
        if decision == "auto":
            return ExtractFieldDecision("not_found", "none_matched")
        return ExtractFieldDecision("review", "none_matched_ambiguous")
    return ExtractFieldDecision(decision, None)
