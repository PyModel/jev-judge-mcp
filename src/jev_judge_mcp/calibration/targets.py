"""Starting AUTO precision targets from ROADMAP P7. Design goals, not claims.

A precision target p is enforced as an error budget: the *upper bound* of the AUTO error rate must be
at most 1 - p. Tools without a stated target (screen, rerank, decide) are not calibrated here.
"""

PRECISION_TARGETS: dict[str, float] = {
    "jev_classify": 0.97,
    "jev_find": 0.97,
    "jev_compare": 0.98,
    "jev_extract": 0.99,
    "jev_verify": 0.99,
    "jev_review": 0.99,
    "jev_gate": 0.995,
}


def error_budget(tool: str) -> float:
    """The maximum allowed upper error bound for `tool`'s AUTO decisions."""
    if tool not in PRECISION_TARGETS:
        raise KeyError(f"{tool} has no AUTO precision target in ROADMAP P7")
    return round(1 - PRECISION_TARGETS[tool], 12)
