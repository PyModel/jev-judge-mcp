"""Literal golden contract for the typed tool outcome (A10).

Expectations are written here. They are not computed by production helpers or by the replay
support code. MCP content[0] stays the exception text. The code, the judge JSON, and the
required hook ask come from the outcome.
"""

import json
import subprocess
from pathlib import Path
from typing import cast

import anyio
import pytest
from mcp.types import TextContent

from jev_judge_mcp.cli import completion_hook_main, judge_main
from jev_judge_mcp.providers import ProviderError, ProviderTimeoutError
from jev_judge_mcp.settings import load_settings
from jev_judge_mcp.tools import TOOLS, Runtime, Toolset
from tests.support.jev import FakeProvider

_VERIFY = {"claims": ["The service listens on 8080."], "evidence": "server.listen(8080)"}
_PUSH = json.dumps({"tool_name": "Bash", "tool_input": {"command": "git push origin main"}})
_AUTH_ASK = (
    '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"ask",'
    '"permissionDecisionReason":"Jev hook: not sure this is safe (auth)."}}\n'
)
_CODE_BLOCK = '{"code":"auth"}'
_CREDENTIALS = (
    "JEV_PROVIDER",
    "TYPESAFE_API_KEY",
    "TYPESAFE_BASE_URL",
    "OPENROUTER_API_KEY",
    "JEV_CLOUDFLARE_API_TOKEN",
    "CLOUDFLARE_API_TOKEN",
    "CLOUDFLARE_ACCOUNT_ID",
    "AI_GATEWAY_API_KEY",
    "JEV_API_KEY",
    "JEV_API_BASE_URL",
)

# Five explicit providers with no usable key, plus a malformed OpenRouter key. The sentences are
# the resolver's, copied here so a helper cannot drift them.
_MISSING = [
    pytest.param("typesafe", {}, "JEV_PROVIDER=typesafe but TYPESAFE_API_KEY is not set.", id="typesafe"),
    pytest.param(
        "openrouter",
        {},
        "JEV_PROVIDER=openrouter but OPENROUTER_API_KEY is not set or not an sk-or- key.",
        id="openrouter",
    ),
    pytest.param(
        "openrouter",
        {"OPENROUTER_API_KEY": "not-a-key"},
        "JEV_PROVIDER=openrouter but OPENROUTER_API_KEY is not set or not an sk-or- key.",
        id="openrouter-malformed",
    ),
    pytest.param(
        "cloudflare",
        {},
        "JEV_PROVIDER=cloudflare but a Cloudflare API token (CLOUDFLARE_API_TOKEN or "
        "JEV_CLOUDFLARE_API_TOKEN) and CLOUDFLARE_ACCOUNT_ID are not both set.",
        id="cloudflare",
    ),
    pytest.param(
        "compatible",
        {},
        "JEV_PROVIDER=compatible but JEV_API_KEY and JEV_API_BASE_URL are not set. "
        "JEV_MCP_MODEL is optional and defaults to jev-latest.",
        id="compatible",
    ),
    pytest.param(
        "vercel",
        {},
        "vercel provider is not supported by the Python server; use typesafe, openrouter, cloudflare or compatible",
        id="vercel",
    ),
]


def _clear(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in _CREDENTIALS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("JEV_MCP_KEY_FILE", "/nonexistent/jev-mcp-key")


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True, capture_output=True)
    (repo / "x.py").write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "x.py"], check=True, capture_output=True)
    commit = ["git", "-C", str(repo), "-c", "user.email=gate@example.invalid", "-c", "user.name=gate test", "commit"]
    subprocess.run([*commit, "-qm", "init"], check=True, capture_output=True)
    (repo / "x.py").write_text("x = 2\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "x.py"], check=True, capture_output=True)
    subprocess.run([*commit, "-qm", "set x to 2"], check=True, capture_output=True)
    (repo / "claims.json").write_text(
        json.dumps({"request": "Set x to 2", "claims": ["The patch sets x to 2."]}),
        encoding="utf-8",
    )
    (repo / "tests.log").write_text("1 passed\n", encoding="utf-8")
    return repo


async def _mcp() -> tuple[str, str, object]:
    toolset = Toolset(Runtime(load_settings()), TOOLS)
    try:
        result = await toolset.call("jev_verify", _VERIFY)
    finally:
        await toolset.aclose()
    first = cast(TextContent, result.content[0]).text
    second = cast(TextContent, result.content[1]).text
    return first, second, result.structured_content


@pytest.mark.parametrize(("provider_name", "extra", "text"), _MISSING)
def test_a_missing_key_is_auth_on_the_wire_the_cli_and_the_required_hook(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    provider_name: str,
    extra: dict[str, str],
    text: str,
) -> None:
    _clear(monkeypatch)
    monkeypatch.setenv("JEV_PROVIDER", provider_name)
    for key, value in extra.items():
        monkeypatch.setenv(key, value)

    first, second, structured = anyio.run(_mcp)
    assert first == text
    assert second == _CODE_BLOCK
    assert structured == {"code": "auth"}

    exit_code = judge_main(["jev_verify"], text=json.dumps(_VERIFY))
    captured = capsys.readouterr()
    assert exit_code == 1
    document = json.loads(captured.out)
    assert document["error"] == {"code": "auth", "message": text}
    assert document["action"] is None
    assert document["unresolved"] is True
    assert document["payload"] == text

    repo = _repo(tmp_path)
    monkeypatch.chdir(repo)
    hook = completion_hook_main(
        [],
        text=_PUSH,
        environ={
            "JEV_HOOK_REQUIRED": "1",
            "JEV_COMPLETION_DIFF": "HEAD~1",
            "JEV_COMPLETION_CLAIMS": "claims.json",
            "JEV_COMPLETION_TESTS": "tests.log",
        },
    )
    hooked = capsys.readouterr()
    assert hook == 0
    assert hooked.out == _AUTH_ASK
    assert "allow" not in hooked.out


def _status(message: str, status: int) -> ProviderError:
    error = ProviderError(message)
    error.status = status
    return error


@pytest.mark.parametrize(
    ("error", "code", "asks"),
    [
        pytest.param(_status("upstream rejected the credential", 401), "auth", True, id="401"),
        pytest.param(_status("slow down", 429), "quota", False, id="429"),
        pytest.param(ProviderTimeoutError("still working"), "timeout", False, id="timeout"),
    ],
)
def test_status_and_timeout_codes_are_literal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    error: BaseException,
    code: str,
    asks: bool,
) -> None:
    """401/429/timeout. The 401 text has no status token, so a text parse would not say auth."""
    provider = FakeProvider({}, error=error)
    toolset = Toolset(Runtime(load_settings(), provider_factory=lambda _settings: provider), TOOLS)

    async def once() -> tuple[str, str, object]:
        try:
            result = await toolset.call("jev_verify", _VERIFY)
        finally:
            await toolset.aclose()
        return (
            cast(TextContent, result.content[0]).text,
            cast(TextContent, result.content[1]).text,
            result.structured_content,
        )

    first, second, structured = anyio.run(once)
    assert first == str(error)
    assert second == '{"code":"' + code + '"}'
    assert structured == {"code": code}

    judge_provider = FakeProvider({}, error=error)
    exit_code = judge_main(["jev_verify"], text=json.dumps(_VERIFY), provider=judge_provider)
    captured = capsys.readouterr()
    assert exit_code == 1
    document = json.loads(captured.out)
    assert document["error"] == {"code": code, "message": str(error)}

    repo = _repo(tmp_path)
    monkeypatch.chdir(repo)
    hook = completion_hook_main(
        [],
        text=_PUSH,
        environ={
            "JEV_HOOK_REQUIRED": "1",
            "JEV_COMPLETION_DIFF": "HEAD~1",
            "JEV_COMPLETION_CLAIMS": "claims.json",
            "JEV_COMPLETION_TESTS": "tests.log",
        },
        provider=FakeProvider({}, error=error),
    )
    hooked = capsys.readouterr()
    assert hook == 0
    if asks:
        assert hooked.out == _AUTH_ASK
    else:
        assert hooked.out == ""
        assert f"error.code={code}\n" in hooked.err
    assert "allow" not in hooked.out
