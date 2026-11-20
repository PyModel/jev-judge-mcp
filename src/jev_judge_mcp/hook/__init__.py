"""Dispatch the short-lived command hooks."""

import sys
from collections.abc import Mapping, Sequence

from jev_judge_mcp.hook.render import fail_open_or_ask, hook_required
from jev_judge_mcp.providers import JevProvider

__all__ = ["fail_open_or_ask", "hook_required", "main"]

_USAGE = "jev-judge-mcp hook: usage: jev-judge-mcp hook gate\n"


def main(
    argv: Sequence[str] | None = None,
    *,
    text: str | None = None,
    environ: Mapping[str, str] | None = None,
    provider: JevProvider | None = None,
) -> int:
    """Run ``hook gate``, or dispatch ``hook screen`` and ``hook compact-cut``.

    Exit 0 after a decision or a pre-call fail-open. Exit 2 on usage.
    """
    args = list(argv or [])
    if args and args[0] == "screen":
        from jev_judge_mcp.hook.screen import main as screen_main

        return screen_main(args[1:], text=text, environ=environ, provider=provider)
    if args == ["compact-cut"]:
        from jev_judge_mcp.hook.compact import compact_cut_main

        return compact_cut_main(args, text=text, provider=provider)
    if args != ["gate"]:
        sys.stderr.write(_USAGE)
        return 2

    from jev_judge_mcp.hook.gate import run

    return run(text=text, environ=environ, provider=provider)
