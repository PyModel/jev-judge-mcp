"""What the three short-lived hooks share: settings, the redacting log handler, provider resolution,
one bounded provider call, cancellation, and close (ADR-0076, ADR-0077).

Each hook keeps its event parsing, its question text, its fail-open wording, and its rendering. This
module writes nothing: it returns an `Evaluation` or a typed `HookFailure` the hook renders. A hook
passes its own `resolve_provider` name in, so a test that patches the hook module's resolver still
reaches the call.
"""

import asyncio
import contextlib
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Literal

import anyio

from jev_judge_mcp.domain.questions import Question
from jev_judge_mcp.keyfile import redaction_values
from jev_judge_mcp.providers import (
    Evaluation,
    JevProvider,
    ProviderConfigError,
    ProviderError,
    ProviderTimeoutError,
    resolve_model,
    resolve_provider,
)
from jev_judge_mcp.settings import Settings, load_settings

PROVIDER_TIMEOUT_SECONDS = 30.0
"""Bound on a hook process's provider call. Retries run inside it (ADR-0057); whatever survives the
budget is still a failure the hook renders as unreachable."""

type HookFailureKind = Literal["config", "timeout", "provider", "cancelled"]


@dataclass(frozen=True, slots=True)
class HookFailure:
    """Why a hook got no evaluation: no provider could be configured, the call timed out, the
    provider failed, or the loop was cancelled. `detail` is the redacted error text, when there is one."""

    kind: HookFailureKind
    detail: str = ""


def prepare(
    provider: JevProvider | None, *, resolve: Callable[[Settings], JevProvider] = resolve_provider
) -> tuple[JevProvider, str] | HookFailure:
    """Settings, the redacting log handler, the model, and the provider: the pre-call wiring.

    The handler is configured before any provider call can log (ADR-0008), and after the hook's
    own usage and stdin gates, so a misconfigured environment gets the hook's one-line answer and
    never a settings traceback. An injected `provider` skips resolution.
    """
    settings = load_settings()
    # Imported here so the short-lived hook process loads the server module only once it runs for real.
    from jev_judge_mcp.server import configure_logging

    configure_logging(settings.log_level, redaction_values(settings))
    model = resolve_model(settings)
    if provider is not None:
        return provider, model
    try:
        return resolve(settings), model
    except ProviderConfigError as error:
        return HookFailure("config", str(error))


def judge(provider: JevProvider, state: str, questions: Mapping[str, Question], model: str) -> Evaluation | HookFailure:
    """One bounded provider call on a fresh loop; the provider is closed on every path."""
    try:
        return anyio.run(_judge, provider, state, questions, model)
    except BaseException as error:
        # The cancel type is only available inside the loop that just exited.
        if isinstance(error, asyncio.CancelledError) or type(error).__name__ == "Cancelled":
            return HookFailure("cancelled")
        raise


async def _judge(
    provider: JevProvider, state: str, questions: Mapping[str, Question], model: str
) -> Evaluation | HookFailure:
    try:
        try:
            return await provider.evaluate(state, questions, model, PROVIDER_TIMEOUT_SECONDS)
        except ProviderTimeoutError as error:
            return HookFailure("timeout", str(error))
        except ProviderError as error:
            return HookFailure("provider", str(error))
    finally:
        with anyio.CancelScope(shield=True):
            with contextlib.suppress(Exception):
                await provider.aclose()
