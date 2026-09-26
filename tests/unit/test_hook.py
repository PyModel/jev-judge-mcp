"""The command hook denies, asks, or writes nothing. It is not an MCP tool."""

import ast
import io
import json
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import ClassVar, override

import anyio
import httpx
import pytest

from jev_judge_mcp.domain import JsonValue, Question, Usage
from jev_judge_mcp.domain.questions import ChoiceQuestion
from jev_judge_mcp.errors import Redactor
from jev_judge_mcp.hook import (
    ESTIMATED_CONFIDENCE_THRESHOLD,
    PROVIDER_TIMEOUT_SECONDS,
    REPORTED_CONFIDENCE_THRESHOLD,
    main,
)
from jev_judge_mcp.providers import Evaluation, JevProvider, ProviderError, ProviderTimeoutError
from jev_judge_mcp.providers.base import ProviderName
from jev_judge_mcp.providers.resolver import resolve_model
from jev_judge_mcp.server import hook_requested, installer_requested
from jev_judge_mcp.server import main as server_main
from jev_judge_mcp.settings import load_settings
from jev_judge_mcp.tools import TOOLS

_HOOK = Path(__file__).resolve().parents[2] / "src" / "jev_judge_mcp" / "hook.py"
_SK = "sk-FAKE000000000000"
_STATE = "production credentials exist in the environment"
_POSIX_MESSAGE = (
    "unsupported platform win32: jev-judge-mcp is POSIX-only; "
    "Windows is unsupported until Windows-specific behavior is implemented and tested"
)


class RecordingProvider(JevProvider):
    """Records the outbound state and returns a scripted choice, or raises."""

    name: ClassVar[ProviderName] = "typesafe"
    label: ClassVar[str] = "Fake"

    def __init__(
        self,
        answer: object = None,
        error: BaseException | None = None,
        *,
        omit_answer: bool = False,
        cancel: bool = False,
    ) -> None:
        super().__init__(Redactor(()))
        self._answer = answer
        self._error = error
        self._omit_answer = omit_answer
        self._cancel = cancel
        self.states: list[JsonValue] = []
        self.questions: Mapping[str, Question] | None = None
        self.model: str | None = None
        self.timeout: float | None = None
        self.closed = False

    @override
    async def evaluate(
        self, state: JsonValue, questions: Mapping[str, Question], model: str, timeout: float | None
    ) -> Evaluation:
        self.states.append(state)
        self.questions = questions
        self.model = model
        self.timeout = timeout
        if self._cancel:
            raise anyio.get_cancelled_exc_class()()
        if self._error is not None:
            raise self._error
        answers: dict[str, object] = {} if self._omit_answer else {"gate": self._answer}
        return Evaluation(answers=answers, usage=Usage(), provider="typesafe", model=model)

    @override
    async def _send(
        self, state: JsonValue, questions: dict[str, JsonValue], model: str, timeout: float | None
    ) -> Evaluation:
        del state, questions, model, timeout
        raise AssertionError("the fake answers from evaluate")

    @override
    async def aclose(self) -> None:
        self.closed = True


def _answered(label: str, probability: float, confidence: float | None = None) -> dict[str, object]:
    other = "deny" if label == "allow" else "allow"
    body: dict[str, object] = {"choice": label, "probabilities": {label: probability, other: 1 - probability}}
    if confidence is not None:
        body["confidence"] = confidence
    return body


def _event(command: str, **extra: object) -> str:
    payload: dict[str, object] = {
        "cwd": "/work/repo",
        "permission_mode": "acceptEdits",
        "tool_name": "Bash",
        "tool_input": {"command": command},
    }
    payload.update(extra)
    return json.dumps(payload)


def test_hook_subcommand_is_not_the_installer() -> None:
    assert hook_requested(["jev-judge-mcp"]) is False
    assert hook_requested(["jev-judge-mcp", "hook"]) is True
    assert hook_requested(["jev-judge-mcp", "install"]) is False
    assert installer_requested(["jev-judge-mcp", "hook"]) is False


def test_confident_deny_prints_deny_and_not_allow(capsys: pytest.CaptureFixture[str]) -> None:
    provider = RecordingProvider(_answered("deny", 0.75, 0.91))
    code = main(["gate"], text=_event(f"rm -rf / --token {_SK}"), environ={}, provider=provider)
    captured = capsys.readouterr()
    assert code == 0
    assert captured.err == ""
    assert "allow" not in captured.out
    decision = json.loads(captured.out)
    assert decision == {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": "Jev hook: denied (confidence 0.91).",
        }
    }
    assert provider.closed
    assert provider.timeout == PROVIDER_TIMEOUT_SECONDS
    assert provider.model == resolve_model(load_settings())
    state = provider.states[0]
    assert isinstance(state, str)
    assert _SK not in state
    assert "[redacted]" in state


def test_confident_allow_writes_no_bytes(capsys: pytest.CaptureFixture[str]) -> None:
    provider = RecordingProvider(_answered("allow", 0.875, 0.9))
    code = main(["gate"], text=_event("echo hi"), environ={}, provider=provider)
    captured = capsys.readouterr()
    assert code == 0
    assert captured.out == ""
    assert captured.err == ""
    assert "permissionDecision" not in captured.out
    assert provider.closed


def test_reported_confidence_at_the_threshold_abstains(capsys: pytest.CaptureFixture[str]) -> None:
    provider = RecordingProvider(_answered("allow", 0.75, REPORTED_CONFIDENCE_THRESHOLD))
    code = main(["gate"], text=_event("echo hi"), environ={}, provider=provider)
    assert code == 0
    assert capsys.readouterr().out == ""


def test_low_reported_confidence_asks_unsure(capsys: pytest.CaptureFixture[str]) -> None:
    provider = RecordingProvider(_answered("allow", 0.75, 0.25))
    code = main(["gate"], text=_event("echo hi"), environ={}, provider=provider)
    captured = capsys.readouterr()
    body = json.loads(captured.out)
    assert code == 0
    assert body["hookSpecificOutput"]["permissionDecision"] == "ask"
    assert "unsure" in body["hookSpecificOutput"]["permissionDecisionReason"]
    assert "allow" not in captured.out


def test_low_confidence_deny_asks_instead_of_denying(capsys: pytest.CaptureFixture[str]) -> None:
    provider = RecordingProvider(_answered("deny", 0.75, 0.25))
    code = main(["gate"], text=_event("echo hi"), environ={}, provider=provider)
    body = json.loads(capsys.readouterr().out)
    assert code == 0
    assert body["hookSpecificOutput"]["permissionDecision"] == "ask"
    assert "unsure" in body["hookSpecificOutput"]["permissionDecisionReason"]


def test_estimated_margin_below_the_hook_threshold_asks(capsys: pytest.CaptureFixture[str]) -> None:
    assert ESTIMATED_CONFIDENCE_THRESHOLD == 0.4
    provider = RecordingProvider(_answered("allow", 0.625))
    code = main(["gate"], text=_event("echo hi"), environ={}, provider=provider)
    body = json.loads(capsys.readouterr().out)
    assert code == 0
    assert body["hookSpecificOutput"]["permissionDecision"] == "ask"
    assert "unsure" in body["hookSpecificOutput"]["permissionDecisionReason"]


def test_estimated_margin_above_the_hook_threshold_abstains(capsys: pytest.CaptureFixture[str]) -> None:
    provider = RecordingProvider(_answered("allow", 0.875))
    code = main(["gate"], text=_event("echo hi"), environ={}, provider=provider)
    assert code == 0
    assert capsys.readouterr().out == ""


def test_provider_error_asks_unreachable(capsys: pytest.CaptureFixture[str]) -> None:
    provider = RecordingProvider(error=ProviderError("upstream down"))
    code = main(["gate"], text=_event(f"echo {_SK}"), environ={}, provider=provider)
    captured = capsys.readouterr()
    body = json.loads(captured.out)
    assert code == 0
    assert body["hookSpecificOutput"]["permissionDecision"] == "ask"
    assert "unreachable" in body["hookSpecificOutput"]["permissionDecisionReason"]
    assert "allow" not in captured.out
    assert _SK not in captured.out
    assert _SK not in captured.err
    assert provider.closed


def test_provider_timeout_asks_unreachable(capsys: pytest.CaptureFixture[str]) -> None:
    provider = RecordingProvider(error=ProviderTimeoutError("timed out"))
    code = main(["gate"], text=_event("echo hi"), environ={}, provider=provider)
    body = json.loads(capsys.readouterr().out)
    assert code == 0
    assert body["hookSpecificOutput"]["permissionDecision"] == "ask"
    assert "unreachable" in body["hookSpecificOutput"]["permissionDecisionReason"]


def test_cancel_asks_unreachable(capsys: pytest.CaptureFixture[str]) -> None:
    provider = RecordingProvider(cancel=True)
    code = main(["gate"], text=_event("echo hi"), environ={}, provider=provider)
    body = json.loads(capsys.readouterr().out)
    assert code == 0
    assert body["hookSpecificOutput"]["permissionDecision"] == "ask"
    assert "unreachable" in body["hookSpecificOutput"]["permissionDecisionReason"]
    assert provider.closed


def test_missing_answer_asks_unreachable(capsys: pytest.CaptureFixture[str]) -> None:
    provider = RecordingProvider(omit_answer=True)
    code = main(["gate"], text=_event("echo hi"), environ={}, provider=provider)
    body = json.loads(capsys.readouterr().out)
    assert code == 0
    assert body["hookSpecificOutput"]["permissionDecision"] == "ask"
    assert "unreachable" in body["hookSpecificOutput"]["permissionDecisionReason"]


def test_malformed_answer_asks_unsure(capsys: pytest.CaptureFixture[str]) -> None:
    provider = RecordingProvider({"choice": "allow"})
    code = main(["gate"], text=_event("echo hi"), environ={}, provider=provider)
    body = json.loads(capsys.readouterr().out)
    assert code == 0
    assert body["hookSpecificOutput"]["permissionDecision"] == "ask"
    assert "unsure" in body["hookSpecificOutput"]["permissionDecisionReason"]


def test_redacts_the_action_and_keeps_operator_state(capsys: pytest.CaptureFixture[str]) -> None:
    provider = RecordingProvider(_answered("deny", 0.75, 0.95))
    description = f"deploy with token {_SK}"
    code = main(
        ["gate"],
        text=_event("echo hi", description=description),
        environ={"JEV_GATE_STATE": _STATE},
        provider=provider,
    )
    assert code == 0
    capsys.readouterr()
    state = provider.states[0]
    assert isinstance(state, str)
    assert _STATE in state
    assert "working in: /work/repo" in state
    assert "Permission mode: acceptEdits" in state
    assert "[redacted]" in state
    assert _SK not in state
    assert provider.questions is not None
    question = provider.questions["gate"]
    assert isinstance(question, ChoiceQuestion)
    assert question.instructions == "Should the agent be allowed to run this proposed action right now?"
    assert question.criteria["allow"] == "The action is safe, reversible or expected, and consistent with the state."
    assert "destructive, off-task" in str(question.criteria["deny"])


def test_operator_state_is_not_pattern_redacted(capsys: pytest.CaptureFixture[str]) -> None:
    provider = RecordingProvider(_answered("allow", 0.875, 0.9))
    code = main(
        ["gate"],
        text=_event("echo hi"),
        environ={"JEV_GATE_STATE": f"note {_SK}"},
        provider=provider,
    )
    assert code == 0
    assert capsys.readouterr().out == ""
    state = provider.states[0]
    assert isinstance(state, str)
    assert _SK in state


def test_string_input_is_redacted_and_missing_fields_default(capsys: pytest.CaptureFixture[str]) -> None:
    provider = RecordingProvider(_answered("deny", 0.75, 0.95))
    code = main(["gate"], text=json.dumps({"tool_input": f"echo {_SK}"}), environ={}, provider=provider)
    assert code == 0
    capsys.readouterr()
    state = provider.states[0]
    assert isinstance(state, str)
    assert "working in: unknown" in state
    assert "Permission mode: default" in state
    assert "Tool: unknown" in state
    assert "[redacted]" in state
    assert _SK not in state


def test_missing_tool_input_is_an_empty_object(capsys: pytest.CaptureFixture[str]) -> None:
    provider = RecordingProvider(_answered("deny", 0.75, 0.95))
    code = main(["gate"], text=json.dumps({"cwd": "/work/repo"}), environ={}, provider=provider)
    assert code == 0
    capsys.readouterr()
    state = provider.states[0]
    assert isinstance(state, str)
    assert "Input: {}" in state


def test_keyboard_interrupt_is_not_an_ask(capsys: pytest.CaptureFixture[str]) -> None:
    provider = RecordingProvider(error=KeyboardInterrupt())
    with pytest.raises(KeyboardInterrupt):
        main(["gate"], text=_event("echo hi"), environ={}, provider=provider)
    captured = capsys.readouterr()
    assert captured.out == ""
    assert provider.closed


def test_blank_operator_state_adds_no_line(capsys: pytest.CaptureFixture[str]) -> None:
    provider = RecordingProvider(_answered("deny", 0.75, 0.95))
    code = main(["gate"], text=_event("echo hi"), environ={"JEV_GATE_STATE": "  \n"}, provider=provider)
    assert code == 0
    capsys.readouterr()
    state = provider.states[0]
    assert isinstance(state, str)
    assert "Permission mode: acceptEdits\nThe agent proposes" in state


def test_bad_stdin_fail_open_does_not_echo_the_body(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["gate"], text=f"not json {_SK}")
    captured = capsys.readouterr()
    assert code == 0
    assert captured.out == ""
    assert captured.err == "jev-judge-mcp hook: stdin was not hook-event JSON\n"
    assert _SK not in captured.err


def test_non_object_stdin_fail_open(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["gate"], text="[]")
    captured = capsys.readouterr()
    assert code == 0
    assert captured.out == ""
    assert captured.err == "jev-judge-mcp hook: stdin was not hook-event JSON\n"


def test_missing_credentials_fail_open_without_a_provider(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
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

    def boom(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise AssertionError("provider constructed")

    monkeypatch.setattr("jev_judge_mcp.providers.resolver.TypeSafeProvider", boom)
    monkeypatch.setattr("jev_judge_mcp.providers.resolver.OpenRouterProvider", boom)
    monkeypatch.setattr("jev_judge_mcp.providers.resolver.CloudflareProvider", boom)
    monkeypatch.setattr("jev_judge_mcp.providers.resolver.CompatibleProvider", boom)
    code = main(["gate"], text=_event(f"echo {_SK}"))
    captured = capsys.readouterr()
    assert code == 0
    assert captured.out == ""
    assert captured.err.startswith("jev-judge-mcp hook: fail-open (")
    assert captured.err.endswith("\n")
    assert captured.err.count("\n") == 1
    assert _SK not in captured.err
    assert "permissionDecision" not in captured.out


def test_fake_path_constructs_no_http_client(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def boom(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise AssertionError("socket")

    monkeypatch.setattr(httpx, "AsyncClient", boom)
    monkeypatch.setattr(httpx, "Client", boom)
    provider = RecordingProvider(_answered("allow", 0.875, 0.9))
    code = main(["gate"], text=_event("echo hi"), environ={}, provider=provider)
    assert code == 0
    assert capsys.readouterr().out == ""


def test_usage_exits_2_with_no_decision(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["other"], text=_event("echo hi"))
    captured = capsys.readouterr()
    assert code == 2
    assert captured.out == ""
    assert captured.err == "jev-judge-mcp hook: usage: jev-judge-mcp hook gate\n"


def test_model_comes_from_process_settings(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setenv("JEV_MCP_MODEL", "jev-1.13")
    provider = RecordingProvider(_answered("allow", 0.875, 0.9))
    code = main(["gate"], text=_event("echo hi"), environ={}, provider=provider)
    assert code == 0
    assert provider.model == "jev-1.13"
    assert capsys.readouterr().out == ""


def _refuse_settings() -> None:
    pytest.fail("settings")


def _refuse_server(settings: object) -> None:
    del settings
    pytest.fail("server")


def _refuse_hook(argv: list[str]) -> int:
    del argv
    pytest.fail("hook")


def test_server_dispatches_hook_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[list[str]] = []

    def fake(argv: list[str]) -> int:
        seen.append(list(argv))
        return 0

    monkeypatch.setattr(sys, "argv", ["jev-judge-mcp", "hook", "gate"])
    monkeypatch.setattr("jev_judge_mcp.server.load_settings", _refuse_settings)
    monkeypatch.setattr("jev_judge_mcp.server.build_server", _refuse_server)
    monkeypatch.setattr("jev_judge_mcp.hook.main", fake)
    with pytest.raises(SystemExit) as caught:
        server_main()
    assert caught.value.code == 0
    assert seen == [["gate"]]


def test_server_rejects_a_bare_hook(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(sys, "argv", ["jev-judge-mcp", "hook"])
    monkeypatch.setattr("jev_judge_mcp.server.load_settings", _refuse_settings)
    with pytest.raises(SystemExit) as caught:
        server_main()
    captured = capsys.readouterr()
    assert caught.value.code == 2
    assert captured.out == ""
    assert "usage" in captured.err


def test_posix_guard_covers_the_hook(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(sys, "argv", ["jev-judge-mcp", "hook", "gate"])
    monkeypatch.setattr("jev_judge_mcp.server.load_settings", _refuse_settings)
    monkeypatch.setattr("jev_judge_mcp.hook.main", _refuse_hook)
    with pytest.raises(SystemExit) as caught:
        server_main()
    assert caught.value.code == _POSIX_MESSAGE


def test_required_flag_asks_when_stdin_exceeds_the_utf16_cap(capsys: pytest.CaptureFixture[str]) -> None:
    from jev_judge_mcp.hook import HOOK_INPUT_UNITS

    body = json.dumps({"tool_name": "Bash", "tool_input": "\U0001f600" * HOOK_INPUT_UNITS})
    code = main(["gate"], text=body, environ={"JEV_HOOK_REQUIRED": "1"})
    captured = capsys.readouterr()
    assert code == 0
    assert json.loads(captured.out)["hookSpecificOutput"]["permissionDecision"] == "ask"
    assert "allow" not in captured.out


def test_required_flag_asks_on_bad_stdin_instead_of_silence(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["gate"], text="not-json", environ={"JEV_HOOK_REQUIRED": "1"})
    captured = capsys.readouterr()
    assert code == 0
    assert captured.err == ""
    decision = json.loads(captured.out)
    assert decision["hookSpecificOutput"]["permissionDecision"] == "ask"
    assert "allow" not in captured.out


def test_hook_module_is_not_the_completion_tool() -> None:
    source = _HOOK.read_text(encoding="utf-8")
    assert "jev_gate" not in source
    tree = ast.parse(source)
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported.append(node.module)
    assert "jev_judge_mcp.policy" not in imported
    assert all(not name.startswith("jev_judge_mcp.policy") for name in imported)


def test_hook_is_not_a_published_tool() -> None:
    names = [tool.name for tool in TOOLS]
    assert len(names) == 11  # the reference ten plus jev_score (ADR-0048)
    assert "hook" not in names


def test_a_bare_hook_with_an_invalid_env_still_prints_usage(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The usage gate answers before any settings parsing: an invalid numeric env cannot traceback."""
    monkeypatch.setattr(sys, "argv", ["jev-judge-mcp", "hook"])
    monkeypatch.setenv("JEV_MCP_MAX_INFLIGHT", "-3")
    monkeypatch.setattr("jev_judge_mcp.server.load_settings", _refuse_settings)
    monkeypatch.setattr("jev_judge_mcp.server.build_server", _refuse_server)
    with pytest.raises(SystemExit) as caught:
        server_main()
    captured = capsys.readouterr()
    assert caught.value.code == 2
    assert "usage" in captured.err
    assert "ValidationError" not in captured.err


def test_hook_gate_with_bad_stdin_and_an_invalid_env_fails_open(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The bad-stdin fail-open gate also answers before settings: exit 0, one stderr line, no traceback."""
    monkeypatch.setattr(sys, "argv", ["jev-judge-mcp", "hook", "gate"])
    monkeypatch.setenv("JEV_MCP_MAX_INFLIGHT", "-3")
    monkeypatch.setattr("jev_judge_mcp.server.load_settings", _refuse_settings)
    monkeypatch.setattr(sys, "stdin", io.StringIO("not-json"))
    with pytest.raises(SystemExit) as caught:
        server_main()
    captured = capsys.readouterr()
    assert caught.value.code == 0
    assert captured.out == ""
    assert "stdin was not hook-event JSON" in captured.err
    assert "ValidationError" not in captured.err
