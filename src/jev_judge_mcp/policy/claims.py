"""Claim policy: per-claim actions, gate reason codes, and decide's requirement contradictions.

Sources: `claimAction` (`lib.ts:344-353`), the gate's reason-code assembly (`index.ts:1471-1482`),
and `contradictsRecommendation` (`lib.ts:153-160`).
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from jev_judge_mcp.policy.actions import Action, require_complete_context, verify_action
from jev_judge_mcp.policy.thresholds import PolicyThresholds

type ClaimVerdict = Literal["verified", "contradicted", "unsupported"]

GATE_REASON_CODES = (
    "incomplete_context",
    "invalid_response",
    "review_escalated",
    "review_required",
    "claims_contradicted",
    "claims_unsupported",
    "claim_confidence_low",
    "claim_confidence_below_auto_accept",
    "caller_note_only",
    "accepted",
)
"""Every gate reason code, in the frozen order they are emitted (`parity-manifest.json` `policy`)."""


def note_blocks_auto(action: str, support: object, evidence: Sequence[Mapping[str, object]]) -> bool:
    """A claim whose only cited support is a caller note cannot be auto (ADR-0067).

    `support` is the source answer as given. The `none` hatch names no item — unless a caller
    item is literally called `none`, in which case a caller note by that name still blocks auto;
    callers pass the raw choice, not the already-blanked `supporting_evidence`.
    """
    if action != "auto" or not isinstance(support, str):
        return False
    item = next((entry for entry in evidence if str(entry.get("id")) == support), None)
    return item is not None and item.get("kind") == "caller_note"


def verify_claim_action(
    confidence: float | None, auto_accept: float, source: object, evidence: Sequence[Mapping[str, object]]
) -> Action:
    """jev_verify's row action: review for an unknown confidence (ADR-0043), `verify_action` otherwise,
    and never auto on a caller note alone (ADR-0067). `source` is the raw source answer."""
    action: Action = "review" if confidence is None else verify_action(confidence, auto_accept)
    return "review" if note_blocks_auto(action, source, evidence) else action


def gate_claim_action(
    verdict: ClaimVerdict,
    confidence: float | None,
    thresholds: PolicyThresholds,
    *,
    truncated: bool,
    source: object,
    evidence: Sequence[Mapping[str, object]],
) -> Action:
    """jev_gate's row action: `claim_action` under the thresholds, never auto over cut context, and
    never auto on a caller note alone (ADR-0067). `source` is the raw source answer."""
    action = require_complete_context(
        claim_action(verdict, confidence, thresholds.auto_accept, thresholds.review_at), truncated
    )
    return "review" if note_blocks_auto(action, source, evidence) else action


def claim_action(verdict: ClaimVerdict, confidence: float | None, auto_accept: float, review_at: float) -> Action:
    """`claimAction`: unknown or low confidence and confident contradictions escalate.

    Only confident verification is auto. Unknown confidence never satisfies a threshold, even a zero one.
    """
    if confidence is None or confidence < review_at:
        return "escalate"
    if verdict == "contradicted" and confidence >= auto_accept:
        return "escalate"
    return "auto" if verdict == "verified" and confidence >= auto_accept else "review"


@dataclass(frozen=True, slots=True)
class ClaimJudgment:
    """A gate claim whose Choice answer validated."""

    verdict: ClaimVerdict
    confidence: float | None


def gate_reason_codes(
    *,
    truncated: bool,
    review_action: Action,
    review_invalid: bool,
    claims: Sequence[ClaimJudgment | None],
    action: Action,
    thresholds: PolicyThresholds,
    caller_note: bool = False,
) -> list[str]:
    """The gate's reason codes, in `GATE_REASON_CODES` order (`index.ts:1472-1482`).

    `claims` holds one entry per claim, `None` for a claim whose answer failed validation. `action` is
    the gate's overall action. A valid claim with unknown confidence counts as confidence -1, so it
    reports `claim_confidence_low` rather than nothing.
    """
    valid = [claim for claim in claims if claim is not None]
    codes: list[str] = []
    if truncated:
        codes.append("incomplete_context")
    if review_invalid or len(valid) < len(claims):
        codes.append("invalid_response")
    if review_action == "escalate":
        codes.append("review_escalated")
    if review_action == "review":
        codes.append("review_required")
    if any(claim.verdict == "contradicted" for claim in valid):
        codes.append("claims_contradicted")
    if any(claim.verdict == "unsupported" for claim in valid):
        codes.append("claims_unsupported")
    confidences = [claim.confidence if claim.confidence is not None else -1 for claim in valid]
    if any(c < thresholds.review_at for c in confidences):
        codes.append("claim_confidence_low")
    if any(thresholds.review_at <= c < thresholds.auto_accept for c in confidences):
        codes.append("claim_confidence_below_auto_accept")
    if caller_note:
        codes.append("caller_note_only")
    if action == "auto":
        codes.append("accepted")
    return codes


@dataclass(frozen=True, slots=True)
class RequirementCheck:
    """One validated jev_decide requirement check: a candidate id, a requirement index, and the answer."""

    candidate: str
    requirement: int
    answer: str


def contradicts_recommendation(checks: Iterable[RequirementCheck], recommended: str) -> list[int]:
    """`contradictsRecommendation`: requirement indexes whose check contradicts the recommended candidate.

    Independent questions may disagree with the recommendation; surface it, do not average it away.
    """
    return [c.requirement for c in checks if c.candidate == recommended and c.answer == "contradicted"]
