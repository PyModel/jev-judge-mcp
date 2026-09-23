"""`jev-judge-mcp setup`: verify a TypeSafe API key with one live call, then store it (ADR-0046).

The key comes from `TYPESAFE_API_KEY` in the environment or a hidden prompt — never from argv, which
a shell history or a process listing would record. The key is proven against the live API with one
cheap noul question before anything is written; a rejected key stores nothing. The stored file is
0600 inside a 0700 directory, and the environment variable keeps precedence over it at resolution.
The key value is never printed.
"""

import argparse
import asyncio
import getpass
import sys
from collections.abc import Callable, Sequence

from jev_judge_mcp.domain import NoulCriteria, NoulQuestion, Question
from jev_judge_mcp.errors import Redactor
from jev_judge_mcp.keyfile import store_key
from jev_judge_mcp.providers import ProviderError
from jev_judge_mcp.providers.resolver import resolve_model
from jev_judge_mcp.providers.typesafe import TypeSafeProvider
from jev_judge_mcp.settings import Settings, load_settings

KEY_URL = "https://console.typesafe.ai/settings/keys"
VERIFY_TIMEOUT_S = 30.0
"""One-shot CLI deadline: setup is not the parity-governed tool path, so it may not hang forever."""

HANDSHAKE: Question = NoulQuestion(
    "Is the sky blue?",
    NoulCriteria("The sky is blue on a clear day", "The sky is another color or the question has no answer"),
)


async def verify_key(settings: Settings, api_key: str) -> None:
    """Prove `api_key` works with one live call. Raises `ProviderError` with redacted text."""
    base_url = None
    if settings.typesafe_base_url is not None:
        base_url = settings.typesafe_base_url.get_secret_value()
    redact = Redactor([*settings.secret_values(), api_key])
    provider = TypeSafeProvider(redact, api_key=api_key, base_url=base_url)
    try:
        await provider.evaluate({}, {"handshake": HANDSHAKE}, resolve_model(settings), VERIFY_TIMEOUT_S)
    finally:
        await provider.aclose()


def main(
    argv: Sequence[str] | None = None,
    *,
    settings: Settings | None = None,
    prompt: Callable[[str], str] = getpass.getpass,
    verify: Callable[[Settings, str], object] | None = None,
) -> int:
    parser = argparse.ArgumentParser(
        prog="jev-judge-mcp setup",
        description="Verify a TypeSafe API key with one live call, then store it for runs without the variable.",
    )
    parser.parse_args(list(sys.argv[1:] if argv is None else argv))
    loaded = settings if settings is not None else load_settings()
    api_key = _candidate_key(loaded, prompt)
    if not api_key:
        print(f"No API key given. Get one at {KEY_URL}; export TYPESAFE_API_KEY or re-run in a terminal.")
        return 1
    check = verify if verify is not None else _run_verification
    try:
        check(loaded, api_key)
    except ProviderError as error:
        print(f"{error}; key NOT stored.")
        return 1
    path = store_key(loaded, api_key)
    source = "TYPESAFE_API_KEY (the variable keeps precedence)" if loaded.typesafe_api_key is not None else "prompt"
    print(f"Key verified ({source}) and stored at {path}.")
    print("Agents started without TYPESAFE_API_KEY now resolve this key automatically.")
    return 0


def _run_verification(loaded: Settings, api_key: str) -> None:
    asyncio.run(verify_key(loaded, api_key))


def _candidate_key(settings: Settings, prompt: Callable[[str], str]) -> str:
    if settings.typesafe_api_key is not None:
        return settings.typesafe_api_key.get_secret_value().strip()
    try:
        return prompt(f"TypeSafe API key ({KEY_URL}): ").strip()
    except (EOFError, KeyboardInterrupt):
        return ""
