"""The judge/gate/hook subcommands configure redacting logging before any tool runs (ADR-0008).

Without it, a toolset `logger.exception` in these short-lived processes reached stderr through
logging's lastResort handler, which has no `RedactingFilter`: error text carrying a configured
secret would print verbatim. Each subcommand dispatch configures logging first; the completion
hook keeps its own stdout/stderr swap (its envelope stays captured, its logs go to real stderr).
"""

import logging
import sys
from typing import Any

import pytest

from jev_judge_mcp.errors import RedactingFilter
from jev_judge_mcp.server import main

_CONFIGURED = "subcommand-fixture-secret-1"

SUBCOMMAND_MAINS = {
    "judge": ("jev_judge_mcp.cli", "judge_main"),
    "gate": ("jev_judge_mcp.cli", "gate_main"),
    "completion-hook": ("jev_judge_mcp.cli", "completion_hook_main"),
    "hook": ("jev_judge_mcp.hook", "main"),
}


@pytest.mark.parametrize("subcommand", sorted(SUBCOMMAND_MAINS))
def test_a_subcommand_configures_redacting_logging_before_it_runs(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], subcommand: str
) -> None:
    module, attribute = SUBCOMMAND_MAINS[subcommand]
    seen: dict[str, Any] = {}

    def fake_main(*_args: object) -> int:
        handlers = logging.getLogger().handlers
        seen["redacting"] = [any(isinstance(f, RedactingFilter) for f in handler.filters) for handler in handlers]
        logging.getLogger("jev_judge_mcp.subcommand").error("token %s", _CONFIGURED)
        return 0

    monkeypatch.setattr(f"{module}.{attribute}", fake_main)
    monkeypatch.setenv("JEV_MCP_HTTP_TOKEN", _CONFIGURED)  # a configured secret for the filter to hold
    monkeypatch.setattr(sys, "argv", ["jev-judge-mcp", subcommand, "rest"])
    root = logging.getLogger()
    saved = (root.handlers[:], root.level)
    try:
        with pytest.raises(SystemExit) as caught:
            main()
        assert caught.value.code == 0
    finally:
        root.handlers[:], root.level = saved

    assert seen["redacting"] == [True]  # one handler, and it redacts
    err = capsys.readouterr().err
    assert "token [redacted]" in err
    assert _CONFIGURED not in err
