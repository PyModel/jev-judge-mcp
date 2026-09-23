"""Offline configuration check for ``jev-judge-mcp doctor``.

Prints the resolved provider, how it was chosen, which credential variable names are set, whether
the key file ``jev-judge-mcp setup`` writes is present (the environment always wins, ADR-0046),
the policy defaults from ``policy/thresholds.py``, and which of the ``mcp__jev__*`` allow rules
the three Claude settings files already carry. A bare ``mcp__jev`` rule covers every tool. The
command reads those files and does not write them. It does not call a provider. It never prints
a credential value. There is no live probe.
"""

import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import cast

import anyio
from pydantic import SecretStr

from jev_judge_mcp.domain.json import decode_json, is_json_object
from jev_judge_mcp.errors import Redactor
from jev_judge_mcp.keyfile import stored_key, stored_key_path
from jev_judge_mcp.policy.thresholds import (
    DEFAULT_AUTO_ACCEPT,
    DEFAULT_CLASSIFY_AUTO_ACCEPT,
    DEFAULT_COMPOSITE_FLOOR,
    DEFAULT_MINIMUM_MARGIN,
    DEFAULT_REVIEW_AT_CAP,
    DEFAULT_SCREEN_BLOCK_AT,
    DEFAULT_SCREEN_REVIEW_AT,
    EXISTS_ABSENT_BELOW,
    EXISTS_FOUND_AT,
    SCREEN_RELEVANCE_SKIP_BELOW,
    SCREEN_SUBSTANCE_SKIP_BELOW,
)
from jev_judge_mcp.providers import JevProvider, ProviderConfigError, resolve_provider
from jev_judge_mcp.settings import Settings, load_settings
from jev_judge_mcp.tools import TOOLS

_USAGE = "jev-judge-mcp doctor: usage: jev-judge-mcp doctor\n"
_BARE_SERVER = "mcp__jev"
_UNSET = "(unset)"

ALLOW_RULES: tuple[str, ...] = tuple(f"mcp__jev__{tool.name}" for tool in TOOLS)

_POLICY: tuple[tuple[str, float], ...] = (
    ("DEFAULT_AUTO_ACCEPT", DEFAULT_AUTO_ACCEPT),
    ("DEFAULT_REVIEW_AT_CAP", DEFAULT_REVIEW_AT_CAP),
    ("DEFAULT_COMPOSITE_FLOOR", DEFAULT_COMPOSITE_FLOOR),
    ("DEFAULT_CLASSIFY_AUTO_ACCEPT", DEFAULT_CLASSIFY_AUTO_ACCEPT),
    ("DEFAULT_MINIMUM_MARGIN", DEFAULT_MINIMUM_MARGIN),
    ("DEFAULT_SCREEN_BLOCK_AT", DEFAULT_SCREEN_BLOCK_AT),
    ("DEFAULT_SCREEN_REVIEW_AT", DEFAULT_SCREEN_REVIEW_AT),
    ("SCREEN_SUBSTANCE_SKIP_BELOW", SCREEN_SUBSTANCE_SKIP_BELOW),
    ("SCREEN_RELEVANCE_SKIP_BELOW", SCREEN_RELEVANCE_SKIP_BELOW),
    ("EXISTS_FOUND_AT", EXISTS_FOUND_AT),
    ("EXISTS_ABSENT_BELOW", EXISTS_ABSENT_BELOW),
)
_KNOWN_EXPLICIT = frozenset({"typesafe", "openrouter", "cloudflare", "compatible"})


def main(argv: Sequence[str] | None = None, *, home: Path | None = None, cwd: Path | None = None) -> int:
    """Print the configuration report. Exit 0 when a provider resolves, 1 when it does not, 2 on usage."""
    if list(argv or []):
        sys.stderr.write(_USAGE)
        return 2
    settings = load_settings()
    root = Path.home() if home is None else home
    work = Path.cwd() if cwd is None else cwd
    name, failure = _resolve(settings)
    report = _report(settings, root, work, name, failure)
    sys.stdout.write(Redactor(settings.secret_values())(report))
    return 0 if name is not None else 1


def claude_settings_files(home: Path, cwd: Path) -> tuple[Path, Path, Path]:
    """The three Claude settings paths jev-use reads: the user file, then this project's two."""
    return (
        home / ".claude" / "settings.json",
        cwd / ".claude" / "settings.json",
        cwd / ".claude" / "settings.local.json",
    )


def _report(settings: Settings, home: Path, cwd: Path, name: str | None, failure: str | None) -> str:
    keys = _set_names(settings)
    paths = claude_settings_files(home, cwd)
    covered = _covered(paths)
    missing = tuple(rule for rule in ALLOW_RULES if rule not in covered)
    stored = stored_key(settings) != ""
    lines = [
        _line("provider", name if name is not None else _UNSET),
        _line("via", _via(settings, name)),
        _line("key", " ".join(keys) if keys else _UNSET),
        _line("key file", f"{'present' if stored else 'absent'} at {stored_key_path(settings)} (env wins)"),
    ]
    if failure is not None:
        lines.append(_line("error", failure))
    lines.append(_line("policy", " ".join(f"{label}={value}" for label, value in _POLICY)))
    lines.append(_line("allow", " ".join(ALLOW_RULES)))
    lines.append(_line("checked", ", ".join(str(path) for path in paths)))
    lines.append(_line("claude", _claude_line(paths, covered)))
    if missing:
        lines.append(_line("missing", " ".join(missing)))
        lines.append(_line("snippet", _snippet(missing)))
    return "".join(lines)


def _line(label: str, value: str) -> str:
    return f"{label:<8}: {value}\n"


def _resolve(settings: Settings) -> tuple[str | None, str | None]:
    """Provider name, or the configuration error. Closing a resolved provider does not send."""
    try:
        provider = resolve_provider(settings)
    except ProviderConfigError as error:
        return None, str(error)
    try:
        return provider.name, None
    finally:
        anyio.run(_close, provider)


async def _close(provider: JevProvider) -> None:
    await provider.aclose()


def _via(settings: Settings, name: str | None) -> str:
    if name is None:
        return "unconfigured"
    explicit = settings.jev_provider.lower()
    if explicit == name and explicit in _KNOWN_EXPLICIT:
        return f"explicit: {name}"
    return f"auto: {_primary_key(settings, name)} found"


def _primary_key(settings: Settings, name: str) -> str:
    if name == "cloudflare":
        return _cloudflare_token_name(settings) or "CLOUDFLARE_API_TOKEN"
    return {
        "typesafe": "TYPESAFE_API_KEY",
        "openrouter": "OPENROUTER_API_KEY",
        "compatible": "JEV_API_KEY",
    }.get(name, name)


def _cloudflare_token_name(settings: Settings) -> str | None:
    if _filled(settings.jev_cloudflare_api_token):
        return "JEV_CLOUDFLARE_API_TOKEN"
    if _filled(settings.cloudflare_api_token):
        return "CLOUDFLARE_API_TOKEN"
    return None


def _set_names(settings: Settings) -> tuple[str, ...]:
    """Credential variable names that are non-empty. Values stay in `Settings`."""
    names: list[str] = []
    if _filled(settings.typesafe_api_key):
        names.append("TYPESAFE_API_KEY")
    if _filled(settings.openrouter_api_key):
        names.append("OPENROUTER_API_KEY")
    if _filled(settings.jev_cloudflare_api_token):
        names.append("JEV_CLOUDFLARE_API_TOKEN")
    if _filled(settings.cloudflare_api_token):
        names.append("CLOUDFLARE_API_TOKEN")
    if settings.cloudflare_account_id:
        names.append("CLOUDFLARE_ACCOUNT_ID")
    if _filled(settings.ai_gateway_api_key):
        names.append("AI_GATEWAY_API_KEY")
    if _filled(settings.jev_api_key):
        names.append("JEV_API_KEY")
    if _filled(settings.jev_api_base_url):
        names.append("JEV_API_BASE_URL")
    return tuple(names)


def _filled(secret: SecretStr | None) -> bool:
    return secret is not None and secret.get_secret_value() != ""


def _covered(paths: Sequence[Path]) -> dict[str, Path]:
    covered: dict[str, Path] = {}
    seen: set[Path] = set()
    for path in paths:
        if path in seen:
            continue
        seen.add(path)
        for rule in _expanded(_allow_rules(path)):
            covered.setdefault(rule, path)
    return covered


def _expanded(rules: Sequence[str]) -> tuple[str, ...]:
    if _BARE_SERVER in rules:
        return ALLOW_RULES
    return tuple(rule for rule in ALLOW_RULES if rule in rules)


def _allow_rules(path: Path) -> tuple[str, ...]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return ()
    try:
        parsed = decode_json(text)
    except ValueError:
        return ()
    if not is_json_object(parsed):
        return ()
    permissions = parsed.get("permissions")
    if not is_json_object(permissions):
        return ()
    allow = permissions.get("allow")
    if not isinstance(allow, list):
        return ()
    rules: list[str] = []
    for item in cast("list[object]", allow):
        if isinstance(item, str):
            rules.append(item)
    return tuple(rules)


def _claude_line(paths: Sequence[Path], covered: dict[str, Path]) -> str:
    if not covered:
        return f"no allow rules in {', '.join(str(path) for path in paths)}"
    by_file: dict[Path, list[str]] = {}
    for rule, path in covered.items():
        by_file.setdefault(path, []).append(rule)
    parts = [f"{path} allows {', '.join(rules)}" for path, rules in by_file.items()]
    return "; ".join(parts)


def _snippet(missing: Sequence[str]) -> str:
    return json.dumps({"permissions": {"allow": list(missing)}})
