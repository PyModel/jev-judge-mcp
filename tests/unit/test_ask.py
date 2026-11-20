"""State assembly, command lifecycle, and request budgeting for jev_ask."""

import os
import sys
import time
from typing import Any

import pytest

from jev_judge_mcp.domain import NoulCriteria, NoulQuestion, ScoreQuestion
from jev_judge_mcp.limits import ASK
from jev_judge_mcp.settings import Settings, load_settings
from jev_judge_mcp.text import length
from jev_judge_mcp.tools import TOOLS, Runtime, Toolset
from jev_judge_mcp.tools.ask_state import (
    Part,
    file_parts,
    own_part,
    question_units,
    run_command,
    split_refusal,
)
from tests.support.jev import FakeProvider, call_tool

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


TOKEN = "ghp_" + "ABCDEF" * 5 + "1234567890"


def _toolset(provider: FakeProvider, settings: Settings | None = None) -> Toolset:
    return Toolset(Runtime(settings or Settings(), provider_factory=lambda _: provider), TOOLS)


def allow_args(**extra: Any) -> dict[str, Any]:
    return {
        "questions": {"q1": {"type": "noul", "instructions": "Did the command check anything?", "criteria": {}}},
        "command": "head a.txt",
        **extra,
    }


# --- the assembler ------------------------------------------------------------------------


def test_own_state_is_redacted_and_measured_but_never_stripped() -> None:
    part = own_part(f"token: {TOKEN}\n\n")
    assert part is not None
    assert TOKEN not in part.text and "[redacted]" in part.text
    assert part.text.endswith("\n\n")
    assert part.units == length(part.text)


def test_own_state_over_the_cap_refuses_typed() -> None:
    with pytest.raises(Exception) as raised:
        own_part("a" * (ASK.state_units_max + 1))
    assert getattr(raised.value, "code", "") == "input_too_large"


def test_whitespace_only_own_state_is_no_part() -> None:
    assert own_part("   \n\t ") is None


def test_file_parts_skip_refused_paths_typed_and_read_duplicates_once(tmp_path: Any, monkeypatch: Any) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "a.txt").write_text("alpha", encoding="utf-8")
    (tmp_path / "b.bin").write_bytes(b"ok\x00")
    parts, skipped = file_parts(["a.txt", "missing.txt", "b.bin", ".env", "a.txt"])
    assert [part.name for part in parts] == ["a.txt"]
    assert [(skip.path, skip.reason) for skip in skipped] == [
        ("missing.txt", "not_found"),
        ("b.bin", "binary_file"),
        (".env", "secret_file"),
    ]


# --- command lifecycle --------------------------------------------------------------------


def test_command_output_is_redacted_and_carries_exit_status_and_stderr() -> None:
    script = f'import sys; print("done"); print("token:{TOKEN}", file=sys.stderr)'
    part = run_command([sys.executable, "-c", script], timeout_seconds=10, output_units_max=1_000)
    assert TOKEN not in part.text and "[redacted]" in part.text
    assert "exit status: 0" in part.text
    assert "--- stderr ---" in part.text and "token:[redacted]" in part.text


def test_command_timeout_kills_the_run_and_refuses_typed() -> None:
    with pytest.raises(Exception) as raised:
        run_command([sys.executable, "-c", "import time; time.sleep(5)"], timeout_seconds=1, output_units_max=1_000)
    assert getattr(raised.value, "code", "") == "command_timeout"
    assert "1-second timeout" in str(raised.value)


def test_command_output_over_the_cap_refuses_after_the_run() -> None:
    with pytest.raises(Exception) as raised:
        run_command([sys.executable, "-c", "print('hello')"], timeout_seconds=10, output_units_max=1)
    assert getattr(raised.value, "code", "") == "output_too_large"


def test_a_background_child_never_holds_the_output_open_or_outlives_the_run() -> None:
    started = time.monotonic()
    script = (
        "import subprocess, sys; print('first'); "
        "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)']); print('second')"
    )
    part = run_command([sys.executable, "-c", script], timeout_seconds=10, output_units_max=1_000)
    assert "first" in part.text and "second" in part.text  # the parent's output is never lost
    assert time.monotonic() - started < 5  # the stray child dies with the run; no join backstop fires


# --- the budget and its Split refusal -----------------------------------------------------


def test_question_units_count_ids_instructions_and_criteria() -> None:
    questions = {
        "q1": NoulQuestion("Is it on?", NoulCriteria(true="on", false="off")),  # 2 + 9 + 2 + 3
        "q2": ScoreQuestion("How bad?", ["low", "high"]),  # 2 + 8 + 3 + 4
    }
    assert question_units(questions) == 33


def test_split_refusal_names_every_part_and_the_first_fit_calls() -> None:
    parts = [Part("state", "s" * 3_000, 3_000), Part("a.md", "a" * 45_000, 45_000), Part("b.md", "b" * 80_000, 80_000)]
    with pytest.raises(Exception) as raised:
        split_refusal(parts, questions_units=800)
    text = str(raised.value)
    assert "state 3,000" in text and "a.md 45,000" in text and "b.md 80,000" in text
    assert "call 1: state, a.md" in text and "call 2: b.md" in text
    assert "Re-ask the same questions on every call" in text


def test_split_refusal_says_what_to_trim_when_the_questions_alone_are_over() -> None:
    with pytest.raises(Exception) as raised:
        split_refusal([Part("state", "s", 1)], questions_units=ASK.request_units_max + 1)
    assert "trim the question set" in str(raised.value)


def test_split_refusal_names_a_part_that_cannot_fit_beside_the_questions() -> None:
    parts = [Part("big.log", "b" * 100_000, 100_000)]
    with pytest.raises(Exception) as raised:
        split_refusal(parts, questions_units=50_000)
    assert "cannot fit beside the questions" in str(raised.value)


async def test_the_composed_request_cap_is_a_strict_greater_than(tmp_path: Any, monkeypatch: Any) -> None:
    """A 20-unit question set over two files: exactly 120,000 units asks; one unit over refuses split."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "a.txt").write_text("a" * 59_990, encoding="utf-8")
    (tmp_path / "b.txt").write_text("b" * 59_990, encoding="utf-8")
    args: dict[str, Any] = {
        "questions": {"q1": {"type": "noul", "instructions": "abcdefghij", "criteria": {}}},
        "paths": ["a.txt", "b.txt"],
    }
    at_cap = await call_tool("jev_ask", args, {"q0": {"noul": 0.5}})
    assert not at_cap.is_error, at_cap.text
    assert at_cap.payload["state_summary"]["units"] == ASK.request_units_max
    (tmp_path / "b.txt").write_text("b" * 59_991, encoding="utf-8")
    over = await call_tool("jev_ask", args, {"q0": {"noul": 0.5}})
    assert over.is_error and over.code == "input_too_large"
    assert "a.txt 59,990" in over.text and "b.txt 59,991" in over.text
    assert over.requests == []


# --- deterministic refusal ordering through the real toolset ------------------------------------------


async def test_a_disabled_or_unallowlisted_command_refuses_before_any_file_is_read(
    tmp_path: Any, monkeypatch: Any
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "a.txt").write_text("alpha", encoding="utf-8")

    def never(*_args: object) -> Any:
        raise AssertionError("files were read before the command's deterministic refusal")

    monkeypatch.setattr("jev_judge_mcp.tools.ask.file_parts", never)
    provider = FakeProvider({"q0": {"noul": 0.5}})
    monkeypatch.delenv("JEV_ASK_COMMANDS", raising=False)
    disabled = _toolset(provider, load_settings())  # JEV_ASK_COMMANDS unset
    monkeypatch.setenv("JEV_ASK_COMMANDS", "1")
    enabled = _toolset(provider, load_settings())
    try:
        off = await disabled.execute("jev_ask", allow_args(paths=["a.txt"]))
        denied = await enabled.execute("jev_ask", allow_args(paths=["a.txt"], command="curl http://x"))
    finally:
        await disabled.aclose()
        await enabled.aclose()
    assert (off.error_code, denied.error_code) == ("command_disabled", "command_refused"), (off.text, denied.text)
    assert provider.requests == []  # zero provider calls on either refusal


def test_scout_012_command_receives_only_operating_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    from unittest.mock import patch

    from jev_judge_mcp.tools.ask_state import scrubbed_environment
    from jev_judge_mcp.tools.base import ToolError

    monkeypatch.setenv("BENIGN_PARENT_MARKER", "sample")
    monkeypatch.setenv("LANG", "C")
    env = scrubbed_environment(Settings())
    assert "BENIGN_PARENT_MARKER" not in env
    assert env["LANG"] == "C" and env["PATH"]
    with patch("subprocess.Popen", side_effect=OSError("fixture")) as spawn, pytest.raises(ToolError):
        run_command([os.path.abspath(sys.executable)], 1, 100)
    assert spawn.call_args.kwargs["env"] == env
