"""The compact cut hook answers which turn starts the live work, or writes nothing."""

import json
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import ClassVar, override

import pytest

from jev_judge_mcp.domain import JsonValue, Question, Usage
from jev_judge_mcp.domain.questions import ChoiceQuestion
from jev_judge_mcp.errors import Redactor
from jev_judge_mcp.hook import PROVIDER_TIMEOUT_SECONDS
from jev_judge_mcp.hook_compact import (
    TRANSCRIPT_TAIL_BYTES,
    TURN_UNITS_MAX,
    TURNS_MAX,
    Turn,
    compact_cut_main,
    parse_turns,
    read_tail,
    window,
)
from jev_judge_mcp.providers import NO_RETRIES, Evaluation, JevProvider, ProviderError, ProviderTimeoutError
from jev_judge_mcp.providers.base import ProviderName
from jev_judge_mcp.server import main as server_main
from jev_judge_mcp.text import length

_TURN = "live work turn"


class ScriptedProvider(JevProvider):
    """Answers ``cut`` from a script, or raises; records the state and the question sent."""

    name: ClassVar[ProviderName] = "typesafe"
    label: ClassVar[str] = "Fake"

    def __init__(self, answer: object = None, error: BaseException | None = None, *, omit: bool = False) -> None:
        super().__init__(Redactor(()), retry=NO_RETRIES)
        self._answer = answer
        self._error = error
        self._omit = omit
        self.states: list[JsonValue] = []
        self.questions: Mapping[str, Question] | None = None
        self.timeout: float | None = None
        self.closed = False

    @override
    async def evaluate(
        self, state: JsonValue, questions: Mapping[str, Question], model: str, timeout: float | None
    ) -> Evaluation:
        self.states.append(state)
        self.questions = questions
        self.timeout = timeout
        if self._error is not None:
            raise self._error
        answers: dict[str, object] = {} if self._omit else {"cut": self._answer}
        return Evaluation(answers=answers, usage=Usage(), provider=self.name, model=model)

    @override
    async def _send(
        self, state: JsonValue, questions: dict[str, JsonValue], model: str, timeout: float | None
    ) -> Evaluation:
        del state, questions, model, timeout
        raise AssertionError("the fake answers from evaluate")

    @override
    async def aclose(self) -> None:
        self.closed = True


def _transcript(*turns: tuple[str, str], extra: str = "") -> str:
    lines = [
        json.dumps({"type": "user", "uuid": identifier, "message": {"role": "user", "content": text}})
        for identifier, text in turns
    ]
    return extra + "\n".join(lines) + "\n"


def _write_transcript(directory: Path, body: str) -> str:
    path = directory / "session.jsonl"
    path.write_text(body, encoding="utf-8")
    return str(path)


def _event(transcript_path: str, *, source: str = "compact", event_name: str = "SessionStart") -> str:
    return json.dumps(
        {
            "session_id": "s",
            "hook_event_name": event_name,
            "source": source,
            "transcript_path": transcript_path,
            "cwd": "/work",
        }
    )


def _answer(
    identifier: str,
    confidence: float | None = None,
    *,
    identifiers: tuple[str, ...] = ("t1", "t2", "t3"),
) -> dict[str, object]:
    """A well-formed Choice over exactly the real turn ids, `identifier` at 0.9."""
    probabilities: dict[str, object] = {identifier: 0.9}
    for other in (item for item in identifiers if item != identifier):
        probabilities[other] = 0.1 / len(identifiers[:-1])
    body: dict[str, object] = {"choice": identifier, "probabilities": probabilities}
    if confidence is not None:
        body["confidence"] = confidence
    return body


def _refuse_settings() -> None:
    pytest.fail("settings loaded on an abstain path")


def _refuse_server(settings: object) -> None:
    del settings
    pytest.fail("server built")


def _refuse_provider(*args: object, **kwargs: object) -> None:
    del args, kwargs
    pytest.fail("provider constructed")


def _block_providers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("jev_judge_mcp.cli.load_settings", _refuse_settings)
    for name in ("TypeSafeProvider", "OpenRouterProvider", "CloudflareProvider", "CompatibleProvider"):
        monkeypatch.setattr(f"jev_judge_mcp.providers.resolver.{name}", _refuse_provider)


@pytest.mark.parametrize(
    ("body", "stderr"),
    [
        ("not-json", "jev-judge-mcp hook: stdin was not hook-event JSON\n"),
        ("[]", "jev-judge-mcp hook: stdin was not hook-event JSON\n"),
        (_event("/nonexistent.jsonl", event_name="PreCompact"), ""),
        (_event("/nonexistent.jsonl", source="startup"), ""),
        (_event("/nonexistent.jsonl", source="resume"), ""),
        (json.dumps({"hook_event_name": "SessionStart", "source": "compact"}), ""),
        (_event(""), ""),
    ],
)
def test_abstain_paths_stay_silent_before_any_provider_work(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], body: str, stderr: str
) -> None:
    """A non-compact event, bad stdin, or a missing transcript path abstains before settings load."""
    _block_providers(monkeypatch)
    assert compact_cut_main(["compact-cut"], text=body) == 0
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == stderr


def test_broken_transcript_files_abstain(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """An unreadable or undecodable transcript file, or one with no usable turns, is silence."""
    _block_providers(monkeypatch)
    binary = tmp_path / "binary.jsonl"
    binary.write_bytes(b"\xff\xfe\x00payload")
    for transcript_path in (str(tmp_path / "missing.jsonl"), str(tmp_path), str(binary)):
        assert compact_cut_main(["compact-cut"], text=_event(transcript_path)) == 0
        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err == ""
    garbage = _write_transcript(tmp_path, "nope\n[1, 2]\n")
    assert compact_cut_main(["compact-cut"], text=_event(garbage)) == 0
    assert capsys.readouterr().out == ""


def test_fewer_than_two_turns_abstains_without_a_provider(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """One real user turn plus a summary and meta lines cannot define a cut point."""
    _block_providers(monkeypatch)
    body = (
        json.dumps({"type": "user", "uuid": "c1", "isCompactSummary": True, "message": {"content": "old summary"}})
        + "\n"
        + json.dumps({"type": "user", "uuid": "m1", "isMeta": True, "message": {"content": "hook reminder"}})
        + "\n"
        + _transcript(("t1", "only real turn"), extra=json.dumps({"type": "assistant", "uuid": "a1"}) + "\n")
    )
    transcript = _write_transcript(tmp_path, body)
    assert compact_cut_main(["compact-cut"], text=_event(transcript)) == 0
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_confident_pick_emits_one_line_naming_a_real_turn(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    provider = ScriptedProvider(_answer("t2", 0.9))
    transcript = _write_transcript(tmp_path, _transcript(("t1", "setup question"), ("t2", _TURN), ("t3", "follow-up")))
    code = compact_cut_main(["compact-cut"], text=_event(transcript), provider=provider)
    captured = capsys.readouterr()
    assert code == 0
    assert captured.err == ""
    decision = json.loads(captured.out)
    assert decision == {
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": f"Compaction cut point: live work starts at turn t2: {_TURN}",
        }
    }
    assert provider.closed
    assert provider.timeout == PROVIDER_TIMEOUT_SECONDS
    assert provider.questions is not None
    question = provider.questions["cut"]
    assert isinstance(question, ChoiceQuestion)
    assert list(question.criteria) == ["t1", "t2", "t3"]
    state = provider.states[0]
    assert isinstance(state, str)
    for text in ("setup question", _TURN, "follow-up"):
        assert text in state
    assert "\n" not in decision["hookSpecificOutput"]["additionalContext"]


def test_window_offers_the_newest_turns_clipped(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    provider = ScriptedProvider(_answer("t22", 0.95))
    body = _transcript(*[(f"t{n}", "x" * 1500) for n in range(23)])
    transcript = _write_transcript(tmp_path, body)
    code = compact_cut_main(["compact-cut"], text=_event(transcript), provider=provider)
    assert code == 0
    capsys.readouterr()
    assert provider.questions is not None
    question = provider.questions["cut"]
    assert isinstance(question, ChoiceQuestion)
    criteria = question.criteria
    assert list(criteria) == [f"t{n}" for n in range(23 - TURNS_MAX, 23)]
    assert all(length(str(description)) == TURN_UNITS_MAX for description in criteria.values())
    state = provider.states[0]
    assert isinstance(state, str)
    assert "[t0]" not in state
    assert "[t1]" not in state
    assert "[t2]" not in state
    assert "[t3]" in state


@pytest.mark.parametrize(
    ("error", "code"),
    [(ProviderError("upstream down"), "provider"), (ProviderTimeoutError("timed out"), "timeout")],
)
def test_provider_failure_is_silence_with_a_code_line(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, error: BaseException, code: str
) -> None:
    provider = ScriptedProvider(error=error)
    transcript = _write_transcript(tmp_path, _transcript(("t1", "a"), ("t2", "b")))
    assert compact_cut_main(["compact-cut"], text=_event(transcript), provider=provider) == 0
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == f"error.code={code}\n"
    assert provider.closed


@pytest.mark.parametrize(
    "answer",
    [
        None,
        {"choice": "t2"},
        _answer("t2", 0.25, identifiers=("t1", "t2")),
        {"choice": "t2", "probabilities": {"t1": 0.5, "t2": 0.5}},
    ],
)
def test_unusable_answers_stay_silent(capsys: pytest.CaptureFixture[str], tmp_path: Path, answer: object) -> None:
    provider = ScriptedProvider(answer, omit=answer is None)
    transcript = _write_transcript(tmp_path, _transcript(("t1", "a"), ("t2", "b")))
    assert compact_cut_main(["compact-cut"], text=_event(transcript), provider=provider) == 0
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_a_fabricated_turn_id_is_silence(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    """The pick is pinned to the offered real ids: an answer over anything else is malformed."""
    provider = ScriptedProvider(_answer("fabricated", 0.99, identifiers=("t1", "t2")))
    transcript = _write_transcript(tmp_path, _transcript(("t1", "a"), ("t2", "b")))
    assert compact_cut_main(["compact-cut"], text=_event(transcript), provider=provider) == 0
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_missing_credentials_fail_open_with_one_stderr_line(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    for key in (
        "JEV_PROVIDER",
        "JEV_MCP_MODEL",
        "TYPESAFE_API_KEY",
        "TYPESAFE_BASE_URL",
        "OPENROUTER_API_KEY",
        "JEV_CLOUDFLARE_API_TOKEN",
        "CLOUDFLARE_API_TOKEN",
        "CLOUDFLARE_ACCOUNT_ID",
        "AI_GATEWAY_API_KEY",
        "JEV_API_KEY",
        "JEV_API_BASE_URL",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr("jev_judge_mcp.providers.resolver.TypeSafeProvider", _refuse_provider)
    monkeypatch.setattr("jev_judge_mcp.providers.resolver.OpenRouterProvider", _refuse_provider)
    monkeypatch.setattr("jev_judge_mcp.providers.resolver.CloudflareProvider", _refuse_provider)
    monkeypatch.setattr("jev_judge_mcp.providers.resolver.CompatibleProvider", _refuse_provider)
    transcript = _write_transcript(tmp_path, _transcript(("t1", "a"), ("t2", "b")))
    assert compact_cut_main(["compact-cut"], text=_event(transcript)) == 0
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith("jev-judge-mcp hook compact-cut: fail-open (")


def test_parse_turns_extracts_usable_user_turns() -> None:
    transcript = "\n".join(
        [
            json.dumps({"type": "summary", "uuid": "s0", "summary": "old summary"}),
            json.dumps({"type": "assistant", "uuid": "a1", "message": {"content": "answer"}}),
            json.dumps({"type": "user", "message": {"content": "no id"}}),
            json.dumps({"type": "user", "uuid": "u1", "message": {"content": "  hello  "}}),
            " broken line ",
            json.dumps({"type": "user", "uuid": "m1", "isMeta": True, "message": {"content": "meta line"}}),
            json.dumps({"type": "user", "uuid": "c1", "isCompactSummary": True, "message": {"content": "the summary"}}),
            json.dumps({"type": "user", "uuid": "w1", "message": {"content": "<command-name>/run</command-name>"}}),
            json.dumps(
                {
                    "type": "user",
                    "uuid": "w2",
                    "message": {"content": "<local-command-stdout>out</local-command-stdout>"},
                }
            ),
            json.dumps(
                {
                    "type": "user",
                    "uuid": "u3",
                    "message": {"content": [{"type": "tool_result", "content": "x"}, {"type": "text", "text": "part"}]},
                }
            ),
            json.dumps({"type": "user", "uuid": "u4", "message": {"content": []}}),
        ]
    )
    turns = parse_turns(transcript)
    assert [(turn.id, turn.text) for turn in turns] == [("u1", "hello"), ("u3", "part")]


def test_server_dispatches_hook_compact_cut(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[list[str]] = []

    def fake(argv: list[str], **kwargs: object) -> int:
        del kwargs
        seen.append(list(argv))
        return 0

    monkeypatch.setattr(sys, "argv", ["jev-judge-mcp", "hook", "compact-cut"])
    monkeypatch.setattr("jev_judge_mcp.server.load_settings", _refuse_server)
    monkeypatch.setattr("jev_judge_mcp.server.build_server", _refuse_server)
    monkeypatch.setattr("jev_judge_mcp.hook_compact.compact_cut_main", fake)
    with pytest.raises(SystemExit) as caught:
        server_main()
    assert caught.value.code == 0
    assert seen == [["compact-cut"]]


def test_wrong_argv_is_a_usage_error(capsys: pytest.CaptureFixture[str]) -> None:
    assert compact_cut_main(["other"]) == 2
    assert compact_cut_main([]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.count("usage: jev-judge-mcp hook compact-cut") == 2


def test_window_keeps_transcript_order() -> None:
    turns = [Turn(id=str(n), text="t") for n in range(5)]
    windowed = window(turns)
    assert [turn.id for turn in windowed] == ["0", "1", "2", "3", "4"]


def test_provider_bound_text_is_redacted_and_the_line_is_not(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """The state and the choice descriptions cross to the provider redacted; the fold-in
    line is the session's own words going back into the same session, so it is verbatim."""
    _sk = "sk-FAKE000000000000"
    provider = ScriptedProvider(_answer("t1", 0.9, identifiers=("t1", "t2")))
    transcript = _write_transcript(tmp_path, _transcript(("t1", f"deploy with token {_sk} now"), ("t2", "other")))
    code = compact_cut_main(["compact-cut"], text=_event(transcript), provider=provider)
    assert code == 0
    captured = capsys.readouterr()
    state = provider.states[0]
    assert isinstance(state, str)
    assert _sk not in state
    assert "[redacted]" in state
    assert provider.questions is not None
    question = provider.questions["cut"]
    assert isinstance(question, ChoiceQuestion)
    assert _sk not in str(question.criteria["t1"])
    decision = json.loads(captured.out)
    line = decision["hookSpecificOutput"]["additionalContext"]
    assert _sk in line


def test_transcript_read_is_bounded_to_the_newest_tail(tmp_path: Path) -> None:
    """Over the window the read starts at the last line boundary: the partial first line drops."""
    tail_lines = (
        '{"type": "user", "uuid": "t1", "message": {"content": "a"}}\n'
        '{"type": "user", "uuid": "t2", "message": {"content": "b"}}\n'
    )
    path = tmp_path / "big.jsonl"
    path.write_bytes(b"x" * (TRANSCRIPT_TAIL_BYTES + 4096) + b"\n" + tail_lines.encode())
    assert read_tail(str(path)) == tail_lines
    small = tmp_path / "small.jsonl"
    small.write_bytes(tail_lines.encode())
    assert read_tail(str(small)) == tail_lines
