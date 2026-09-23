"""`jev-judge-mcp setup`: verify with one live call, then store (ADR-0046). Never a key in argv."""

import io
import os
import stat
import sys
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import cast

import pytest

from jev_judge_mcp.providers import ProviderError
from jev_judge_mcp.server import setup_requested
from jev_judge_mcp.settings import Settings, load_settings
from jev_judge_mcp.setup import main

KEY = "sk-live-setup-test"


@pytest.fixture(autouse=True)
def isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    path = tmp_path / "config" / "key"
    monkeypatch.setenv("JEV_MCP_KEY_FILE", str(path))
    return path


def run(
    monkeypatch: pytest.MonkeyPatch,
    isolated: Path,
    *,
    prompt_input: str = "",
    verify: Callable[[Settings, str], object] | None = None,
    env_key: str | None = None,
) -> tuple[int, str]:
    if env_key is not None:
        monkeypatch.setenv("TYPESAFE_API_KEY", env_key)
    buffer = io.StringIO()
    stdout = sys.stdout
    sys.stdout = buffer
    try:
        code = main(
            [],
            settings=load_settings(),
            prompt=lambda _: prompt_input,
            **({} if verify is None else {"verify": verify}),
        )
    finally:
        sys.stdout = stdout
    return code, buffer.getvalue()


def test_no_key_on_a_non_interactive_shell(monkeypatch: pytest.MonkeyPatch, isolated: Path) -> None:
    def eof(_: str) -> str:
        raise EOFError

    buffer = io.StringIO()
    stdout = sys.stdout
    sys.stdout = buffer
    try:
        code = main([], settings=load_settings(), prompt=eof)
    finally:
        sys.stdout = stdout
    assert code == 1
    assert "No API key given" in buffer.getvalue()
    assert not isolated.exists()


def test_a_rejected_key_stores_nothing(monkeypatch: pytest.MonkeyPatch, isolated: Path) -> None:
    def refuse(_: Settings, key: str) -> object:
        assert key == KEY
        # A provider error is already redacted when it leaves the provider (ADR-0008); the
        # candidate key joined the verify redactor, so it cannot be in this text either.
        raise ProviderError("TypeSafe 401: key rejected")

    code, text = run(monkeypatch, isolated, prompt_input=KEY, verify=refuse)
    assert code == 1
    assert "key NOT stored" in text
    assert KEY not in text  # the candidate joined the redaction set before the error was raised
    assert not isolated.exists()


def test_a_verified_prompted_key_is_stored_privately(monkeypatch: pytest.MonkeyPatch, isolated: Path) -> None:
    seen: list[str] = []

    def accept(_: Settings, key: str) -> object:
        seen.append(key)
        return None

    code, text = run(monkeypatch, isolated, prompt_input=KEY, verify=accept)
    assert code == 0
    assert seen == [KEY]
    assert "stored at" in text
    assert KEY not in text
    assert isolated.read_text(encoding="utf-8") == KEY + "\n"
    assert stat.S_IMODE(os.stat(isolated).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(isolated.parent).st_mode) == 0o700


def test_an_environment_key_is_verified_and_stored_with_precedence_note(
    monkeypatch: pytest.MonkeyPatch, isolated: Path
) -> None:
    def accept(_: Settings, key: str) -> object:
        assert key == KEY
        return None

    code, text = run(monkeypatch, isolated, verify=accept, env_key=KEY)
    assert code == 0
    assert "keeps precedence" in text
    assert isolated.read_text(encoding="utf-8") == KEY + "\n"


def test_the_handshake_redacts_the_candidate_key(monkeypatch: pytest.MonkeyPatch, isolated: Path) -> None:
    """The default verifier builds a TypeSafe provider carrying the candidate key in its redactor."""
    from jev_judge_mcp import setup as setup_module

    captured: dict[str, object] = {}

    class StubProvider:
        def __init__(self, redact: Callable[[str], str], *, api_key: str, base_url: str | None) -> None:
            captured["redacted"] = redact(f"bearer {api_key}")

        async def evaluate(self, state: object, questions: object, model: str, timeout: float | None) -> object:
            captured["model"] = model
            captured["timeout"] = timeout
            captured["questions"] = dict(cast(Mapping[str, object], questions))
            return object()

        async def aclose(self) -> None:
            return None

    monkeypatch.setattr(setup_module, "TypeSafeProvider", StubProvider)
    code, _ = run(monkeypatch, isolated, prompt_input=KEY)
    assert code == 0
    assert captured["redacted"] == "bearer [redacted]"
    assert captured["model"] == "jev-latest"
    assert isinstance(captured["timeout"], float)
    assert list(cast(dict[str, object], captured["questions"])) == ["handshake"]


def test_a_configured_base_url_reaches_the_verify_provider(monkeypatch: pytest.MonkeyPatch, isolated: Path) -> None:
    from jev_judge_mcp import setup as setup_module

    monkeypatch.setenv("TYPESAFE_BASE_URL", "https://proxy.example/v1")
    seen: dict[str, object] = {}

    class StubProvider:
        def __init__(self, redact: Callable[[str], str], *, api_key: str, base_url: str | None) -> None:
            seen["base_url"] = base_url

        async def evaluate(self, state: object, questions: object, model: str, timeout: float | None) -> object:
            return object()

        async def aclose(self) -> None:
            return None

    monkeypatch.setattr(setup_module, "TypeSafeProvider", StubProvider)
    code, _ = run(monkeypatch, isolated, prompt_input=KEY)
    assert code == 0
    assert seen["base_url"] == "https://proxy.example/v1"


def test_setup_is_a_subcommand_and_nothing_else_is() -> None:
    assert setup_requested(["jev-judge-mcp", "setup"]) is True
    assert setup_requested(["jev-judge-mcp"]) is False
    assert setup_requested(["jev-judge-mcp", "install"]) is False
    assert setup_requested(["jev-judge-mcp", "--help"]) is False
