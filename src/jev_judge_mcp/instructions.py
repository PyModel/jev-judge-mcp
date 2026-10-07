"""Server instructions built from the tool registry.

The string is what a client loads when tool schemas are deferred. It names each published
tool once, says when to call ``jev_gate``, that the caller must honor ``action``, and that a
patch goes in as the real diff.
No threshold numbers, no secrets, no harness-specific names.
"""

from collections.abc import Sequence

_WHEN = (
    "jev_gate is the recommended final judgment before claiming done on a diff, "
    "or before opening or merging a pull request, unless tests, type checks, build, lint, "
    "or another explicit acceptance criterion already settle completion."
)
_HONOR = (
    "Honor action. Do not grep verdict. "
    "auto means the row stands and you may proceed. "
    "review means you still own the row and must confirm it before proceeding. "
    "escalate means stop and decide yourself. This server does not call another model. "
    "Ignoring escalate is not a pass."
)
_PACK = (
    "For a change in git, jev_gate_range reads the range itself. jev_gate and jev_review read no files: "
    "pass the real git diff, whole or as a {path, patch} file list, never an excerpt or summary. "
    "Re-sending with the full diff or raw logs is a new call, not a re-ask."
)
ON_DEMAND = (
    "Jev is invoked when an unresolved judgment earns a model decision. "
    "Deterministic evidence takes precedence; Jev is not a mandatory ceremony. "
    "High-value calls: before a done claim, jev_gate, unless tests, type checks, build, lint, "
    "or another explicit acceptance criterion already settle completion; "
    "before reading fetched or pasted external text, jev_screen; "
    "checking another agent's report or research claims, jev_verify. "
    "Skip it when the answer is already determined by a test, type-check, or the code itself; "
    "when the choice is trivial or cheap to reverse; "
    "when the question cannot be enumerated into bounded options; "
    "or when the same unchanged decision was already asked."
)
_SKILLS = (
    "Skills: jev-mcp (resource jev-skill://jev-mcp/SKILL.md, prompt jev-mcp) routes these tools. "
    "jev (resource jev-skill://jev/SKILL.md, prompt jev) is for an app that calls the Jev API, not these tools. "
) + ON_DEMAND


def server_instructions(names: Sequence[str]) -> str:
    """One short instructions string. ``names`` is the registry order, each tool once."""
    if len(names) != len(set(names)):
        raise ValueError("tool names must be unique")
    listed = ", ".join(names)
    return f"Tools: {listed}. {_WHEN} {_HONOR} {_PACK} {_SKILLS}"
