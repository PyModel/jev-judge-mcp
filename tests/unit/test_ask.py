"""jev_ask mechanics at their owning boundaries: the state assembler, the gated command, the
question-unit budget with its Split refusal, and the gate-before-command ordering (ADR-0077).

The payload schema, refusal typing at the argument boundary, and registry order are
`tests/contract/test_ask.py`; the hook's own judge is `tests/unit/test_hook.py` — what is asserted
here is ask's stricter allow rule over the imported questions, not the hook's outcome logic.
"""

import time
from typing import Any, cast

import pytest

from jev_judge_mcp.domain import NoulCriteria, NoulQuestion, ScoreQuestion, Usage
from jev_judge_mcp.hook import DESTRUCTIVE_ID, EFFECT_ID
from jev_judge_mcp.hook_render import FINAL_BLOCK_NOTICE
from jev_judge_mcp.limits import ASK
from jev_judge_mcp.providers import Evaluation
from jev_judge_mcp.settings import Settings, load_settings
from jev_judge_mcp.text import length
from jev_judge_mcp.tools import TOOLS, Runtime, Toolset
from jev_judge_mcp.tools.ask_state import (
    Part,
    command_refusal,
    file_parts,
    gate_questions,
    gate_state,
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
EFFECT_KEYS = ("read_only", "reversible", "irreversible")


def effect_answer(choice: str, confidence: float | None = 0.9) -> dict[str, Any]:
    others = [key for key in EFFECT_KEYS if key != choice]
    probabilities = {choice: 0.9, others[0]: 0.06, others[1]: 0.04}
    answer: dict[str, Any] = {"choice": choice, "probabilities": probabilities}
    if confidence is not None:
        answer["confidence"] = confidence
    return answer


GATE_ALLOW = {"effect": effect_answer("read_only"), "destructive_intent": {"noul": 0.01}}


class GatedProvider(FakeProvider):
    """The gate call and the ask call answered separately, both recorded."""

    def __init__(self, gate_answers: dict[str, Any], ask_answers: dict[str, Any]) -> None:
        super().__init__({})
        self._gate, self._ask = gate_answers, ask_answers

    async def _send(self, state: Any, questions: dict[str, Any], model: str, timeout: float | None) -> Evaluation:
        self.requests.append((state, questions))
        answers = self._gate if EFFECT_ID in questions else self._ask
        return Evaluation(answers, Usage(1, 1), self.name, model)


def _toolset(provider: FakeProvider, settings: Settings | None = None) -> Toolset:
    return Toolset(Runtime(settings or Settings(), provider_factory=lambda _: provider), TOOLS)


def allow_args(**extra: Any) -> dict[str, Any]:
    return {
        "questions": {"q1": {"type": "noul", "instructions": "Did the command check anything?", "criteria": {}}},
        "command": "printf judge-me",
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


# --- the gated command --------------------------------------------------------------------


def test_command_output_is_redacted_and_carries_exit_status_and_stderr() -> None:
    part = run_command(f"echo token:{TOKEN} >&2; echo done", timeout_seconds=10, output_units_max=1_000)
    assert TOKEN not in part.text and "[redacted]" in part.text
    assert "exit status: 0" in part.text
    assert "--- stderr ---" in part.text and "token:[redacted]" in part.text


def test_command_timeout_kills_the_run_and_refuses_typed() -> None:
    with pytest.raises(Exception) as raised:
        run_command("sleep 5", timeout_seconds=1, output_units_max=1_000)
    assert getattr(raised.value, "code", "") == "command_timeout"
    assert "1-second timeout" in str(raised.value)


def test_command_output_over_the_cap_refuses_after_the_run() -> None:
    with pytest.raises(Exception) as raised:
        run_command("echo hello", timeout_seconds=10, output_units_max=1)
    assert getattr(raised.value, "code", "") == "output_too_large"


def test_a_background_child_never_holds_the_output_open_or_outlives_the_run() -> None:
    started = time.monotonic()
    part = run_command("echo first; sleep 30 & echo second", timeout_seconds=10, output_units_max=1_000)
    assert "first" in part.text and "second" in part.text  # the shell's own output is never lost
    assert time.monotonic() - started < 5  # the stray child dies with the run; no join backstop fires


def test_the_command_refusal_matrix_over_the_gate_answers() -> None:
    malformed = command_refusal({"effect": {"noul": 1}, "destructive_intent": {"noul": 0.1}})
    assert malformed is not None and "malformed" in malformed
    refusal = command_refusal({"effect": effect_answer("irreversible"), "destructive_intent": {"noul": 0.1}})
    assert refusal is not None and "irreversible" in refusal
    refusal = command_refusal({"effect": effect_answer("read_only"), "destructive_intent": {"noul": 0.9}})
    assert refusal is not None and "destroying work" in refusal
    assert command_refusal({"effect": effect_answer("read_only"), "destructive_intent": {"noul": 0.1}}) is None


def test_an_unsure_or_reversible_effect_is_refused_with_the_final_block_notice() -> None:
    # No reported confidence: the hook's margin estimate (0.0 here) is below its own floor.
    unsure = {"choice": "read_only", "probabilities": {"read_only": 0.5, "reversible": 0.5, "irreversible": 0.0}}
    refusal = command_refusal({"effect": unsure, "destructive_intent": {"noul": 0.1}})
    assert refusal is not None and "could not tell" in refusal and FINAL_BLOCK_NOTICE in refusal
    refusal = command_refusal({"effect": effect_answer("reversible"), "destructive_intent": {"noul": 0.0}})
    assert refusal is not None and "reversible" in refusal and FINAL_BLOCK_NOTICE in refusal


def test_the_gate_questions_are_the_hook_pair_and_the_state_redacts_the_command() -> None:
    questions = gate_questions()
    assert set(questions) == {EFFECT_ID, DESTRUCTIVE_ID}
    state = gate_state(f"echo {TOKEN}")
    assert TOKEN not in state and "Bash" in state


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
    at_cap = await call_tool("jev_ask", args, {"q1": {"noul": 0.5}})
    assert not at_cap.is_error, at_cap.text
    assert at_cap.payload["state_summary"]["units"] == ASK.request_units_max
    (tmp_path / "b.txt").write_text("b" * 59_991, encoding="utf-8")
    over = await call_tool("jev_ask", args, {"q1": {"noul": 0.5}})
    assert over.is_error and over.code == "input_too_large"
    assert "a.txt 59,990" in over.text and "b.txt 59,991" in over.text
    assert over.requests == []


# --- gate-first ordering through the real toolset ------------------------------------------


async def test_a_disabled_or_denylisted_command_refuses_before_any_file_is_read(
    tmp_path: Any, monkeypatch: Any
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "a.txt").write_text("alpha", encoding="utf-8")

    def never(_paths: Any) -> Any:
        raise AssertionError("files were read before the command's deterministic refusal")

    monkeypatch.setattr("jev_judge_mcp.tools.ask.file_parts", never)
    provider = GatedProvider(dict(GATE_ALLOW), {"q1": {"noul": 0.5}})
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


async def test_a_refused_command_never_runs_and_never_reaches_the_ask(tmp_path: Any, monkeypatch: Any) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("JEV_ASK_COMMANDS", "1")
    gate = {"effect": effect_answer("irreversible"), "destructive_intent": {"noul": 0.1}}
    provider = GatedProvider(gate, {"q1": {"noul": 0.9}})
    toolset = _toolset(provider, load_settings())
    try:
        outcome = await toolset.execute("jev_ask", allow_args())
    finally:
        await toolset.aclose()
    assert outcome.is_error
    assert outcome.error_code == "command_refused"
    assert FINAL_BLOCK_NOTICE in outcome.text
    assert not (tmp_path / "marker.txt").exists()  # zero execution
    assert len(provider.requests) == 1  # the gate judged; the ask never happened
    assert EFFECT_ID in provider.requests[0][1]


async def test_an_allowed_command_runs_after_the_gate(tmp_path: Any, monkeypatch: Any) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("JEV_ASK_COMMANDS", "1")
    provider = GatedProvider(dict(GATE_ALLOW), {"q1": {"noul": 0.95}})
    args: dict[str, Any] = {
        "questions": {"q1": {"type": "noul", "instructions": "Did it produce output?", "criteria": {}}},
        "command": "printf run-ok > marker.txt; printf print-ok",
    }
    toolset = _toolset(provider, load_settings())
    try:
        outcome = await toolset.execute("jev_ask", args)
    finally:
        await toolset.aclose()
    assert not outcome.is_error, outcome.text
    assert (tmp_path / "marker.txt").exists()  # the command ran, after the gate
    _, gate_questions_sent = provider.requests[0]
    ask_state_sent, ask_questions_sent = provider.requests[1]
    assert EFFECT_ID in gate_questions_sent and set(ask_questions_sent) == {"q1"}
    assert "print-ok" in cast(str, cast(dict[str, object], ask_state_sent)["command output"])
    payload = cast(dict[str, Any], outcome.payload)
    assert payload["state_summary"]["output"] > 0
    assert payload["usage"] == {"input_tokens": 2, "output_tokens": 2}  # gate + ask, summed
