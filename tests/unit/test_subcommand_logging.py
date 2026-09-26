"""The judge/gate/hook subcommands configure redacting logging before any tool runs (ADR-0008).

Without it, a toolset `logger.exception` in these short-lived processes reached stderr through
logging's lastResort handler, which has no `RedactingFilter`: error text carrying a configured
secret would print verbatim. `judge`, `gate`, and `completion-hook` configure logging in the
`server.main` dispatch before the subcommand runs. `hook gate` configures it inside its own
`main`, after the usage and stdin fail-open gates, so a misconfigured environment still gets
their one-line answers; the completion hook keeps its own stdout/stderr swap.
"""

import io
import json
import logging
import sys
from typing import Any

import pytest

from jev_judge_mcp.errors import RedactingFilter
from jev_judge_mcp.providers import ProviderConfigError
from jev_judge_mcp.server import main

_CONFIGURED = "subcommand-fixture-secret-1"

SUBCOMMAND_MAINS = {
    "judge": ("jev_judge_mcp.cli", "judge_main"),
    "gate": ("jev_judge_mcp.cli", "gate_main"),
    "completion-hook": ("jev_judge_mcp.cli", "completion_hook_main"),
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


def test_hook_gate_configures_redacting_logging_before_the_provider_leg(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`hook gate` configures logging inside its own main, past the usage and stdin gates (H1).

    The provider resolution below stands in for the first thing that can log: by the time it
    runs, the root handler is the redacting one, and the emitted secret never reaches stderr.
    """
    seen: dict[str, Any] = {}

    def fake_resolve_provider(_settings: object) -> object:
        handlers = logging.getLogger().handlers
        seen["redacting"] = [any(isinstance(f, RedactingFilter) for f in handler.filters) for handler in handlers]
        logging.getLogger("jev_judge_mcp.subcommand").error("token %s", _CONFIGURED)
        raise ProviderConfigError("no credentials in this test")

    event = json.dumps({"tool_name": "Bash", "tool_input": {"command": "echo hi"}})
    monkeypatch.setattr("jev_judge_mcp.hook.resolve_provider", fake_resolve_provider)
    monkeypatch.setenv("JEV_MCP_HTTP_TOKEN", _CONFIGURED)  # a configured secret for the filter to hold
    monkeypatch.setattr(sys, "argv", ["jev-judge-mcp", "hook", "gate"])
    monkeypatch.setattr(sys, "stdin", io.StringIO(event))
    root = logging.getLogger()
    saved = (root.handlers[:], root.level)
    try:
        with pytest.raises(SystemExit) as caught:
            main()
        assert caught.value.code == 0  # no credentials: fail-open, one stderr line, empty stdout
    finally:
        root.handlers[:], root.level = saved

    assert seen["redacting"] == [True]  # one handler, and it redacts
    err = capsys.readouterr().err
    assert "token [redacted]" in err
    assert _CONFIGURED not in err
