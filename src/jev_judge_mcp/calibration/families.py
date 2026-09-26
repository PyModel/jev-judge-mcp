"""The one family-ordering invariant every family-aware split uses (ADR-0070).

`evals.calibration.split` (the 60/20/20 eval split) and `jev_judge_mcp.calibrate` (the CLI's
selection/held-out split) order families the same way so both are deterministic across processes
and repeats; the rule lives once, here, instead of being re-derived per caller.
"""

import hashlib


def family_order(family: str, salt: str) -> str:
    """`sha256(salt + NUL + family)`: stable across processes, unlike `hash`."""
    return hashlib.sha256(f"{salt}\0{family}".encode()).hexdigest()
