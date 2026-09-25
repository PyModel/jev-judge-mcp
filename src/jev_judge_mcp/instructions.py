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


def server_instructions(names: Sequence[str]) -> str:
    """One short instructions string. ``names`` is the registry order, each tool once."""
    if len(names) != len(set(names)):
        raise ValueError("tool names must be unique")
    listed = ", ".join(names)
    return f"Tools: {listed}. {_WHEN} {_HONOR}"
