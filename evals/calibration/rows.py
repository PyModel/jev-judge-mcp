"""Per-tool `(score, correct)` rows for AUTO threshold calibration, read from `judgments` (ADR-0028).

`score` is the scalar the tool compares against its auto_accept threshold; `correct` is whether the
judgment matched gold. jev_find has no AUTO decision today: its row is the top candidate's probability,
so a find operating point is a proposal, and adopting one would be a Sanctioned Divergence. Secondary
gates (classify/compare/extract `minimum_margin`, review's composite floor) are not jointly calibrated.
jev_gate combines a review and per-claim thresholds, so it has no single score here: its report carries
the false-AUTO rate and its upper bound instead.
"""

from collections.abc import Sequence

from evals.scorers.tools import Example, judgments

Row = tuple[float, bool]

CALIBRATED_TOOLS = ("jev_verify", "jev_classify", "jev_compare", "jev_extract", "jev_find", "jev_review")


def calibration_rows(tool: str, examples: Sequence[Example]) -> dict[str, Row]:
    """Keyed rows (`case` or `case/item`), so repeated runs of one case line up for flip rates."""
    if tool not in CALIBRATED_TOOLS:
        raise KeyError(f"{tool} has no single-score AUTO calibration")
    return {j.key: (j.score, j.correct) for j in judgments(tool, examples) if j.score is not None}
