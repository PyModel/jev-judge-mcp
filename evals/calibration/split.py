"""60/20/20 dev/calibration/locked-test split by source family, never by row.

Families are ordered by the SHA-256 of `salt + NUL + family` (stable across processes, unlike `hash`),
then each whole family goes to the split furthest below its row target (target share x total rows minus
rows already assigned; ties go to the earlier split). A family never spans two splits, so when one
family is larger than a split's share the ratios are only approximate, and with fewer than three
families some splits stay empty.
"""

from collections.abc import Callable, Sequence

from jev_judge_mcp.calibration.families import family_order

SPLITS = ("dev", "calibration", "locked_test")
RATIOS = (0.6, 0.2, 0.2)


def _order(family: str, salt: str) -> str:
    return family_order(family, salt)


def split_by_family[T](rows: Sequence[T], family: Callable[[T], str], salt: str = "") -> dict[str, list[T]]:
    groups: dict[str, list[T]] = {}
    for row in rows:
        groups.setdefault(family(row), []).append(row)
    splits: dict[str, list[T]] = {name: [] for name in SPLITS}
    total = len(rows)
    for name in sorted(groups, key=lambda f: _order(f, salt)):
        deficits = [ratio * total - len(splits[split]) for split, ratio in zip(SPLITS, RATIOS, strict=True)]
        target = SPLITS[deficits.index(max(deficits))]
        splits[target].extend(groups[name])
    return splits
