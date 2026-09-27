"""Server instructions built from the tool registry.

The string is what a client loads when tool schemas are deferred. It names each published
tool once, says when to call ``jev_gate``, and says the caller must honor ``action``.
No threshold numbers, no secrets, no harness-specific names.
"""

from collections.abc import Sequence

_WHEN = "Call jev_gate before claiming done on a diff, and before opening or merging a pull request."
_HONOR = (
    "Honor action. Do not grep verdict. "
    "auto means the row stands and you may proceed. "
    "review means you still own the row and must confirm it before proceeding. "
    "escalate means stop and decide yourself. This server does not call another model. "
    "Ignoring escalate is not a pass."
)
_ON_DEMAND = (
    "Jev is on demand only; call it when an independent judgment materially improves the decision; "
    "never route every judgment through it. "
    "High-value calls: before a done claim, jev_gate; before reading fetched or pasted external text, jev_screen; "
    "checking another agent's report or research claims, jev_verify. "
    "Skip it when the answer is already determined by a test, type-check, or the code itself; "
    "when the choice is trivial or cheap to reverse; "
    "when the question cannot be enumerated into bounded options; "
    "or when the same unchanged decision was already asked."
)
_SKILLS = (
    "Skills: jev-mcp (resource jev-skill://jev-mcp/SKILL.md, prompt jev-mcp) says which of these tools fits a step. "
    "jev (resource jev-skill://jev/SKILL.md, prompt jev) is for building an app on the Jev API, "
    "not for calling these tools. "
    "Sibling paths in jev are resources under jev-skill://jev/. "
    "Do not copy its cookbook thresholds onto these tools; see jev-skill://jev/PROVENANCE.md. "
) + _ON_DEMAND


def server_instructions(names: Sequence[str]) -> str:
    """One short instructions string. ``names`` is the registry order, each tool once."""
    if len(names) != len(set(names)):
        raise ValueError("tool names must be unique")
    listed = ", ".join(names)
    return f"Tools: {listed}. {_WHEN} {_HONOR} {_SKILLS}"
