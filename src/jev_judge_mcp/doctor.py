"""Offline configuration check for ``jev-judge-mcp doctor``.

Prints the resolved provider, how it was chosen, which credential variable names are set, whether
the key file ``jev-judge-mcp setup`` writes is present (the environment always wins, ADR-0046),
the policy defaults from ``policy/thresholds.py``, and which of the ``mcp__jev__*`` allow rules the
three Claude settings files already carry. A bare ``mcp__jev`` rule covers every tool. The
command reads those files and does not write them. It does not call a provider. It never prints
a credential value. There is no live probe.
"""

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import cast

from pydantic import SecretStr

from jev_judge_mcp.domain.json import decode_json, is_json_object
from jev_judge_mcp.fsutil import read_bounded
from jev_judge_mcp.keyfile import redaction_values, stored_key, stored_key_path
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
    POLICY_VERSION,
    SCREEN_RELEVANCE_SKIP_BELOW,
    SCREEN_SUBSTANCE_SKIP_BELOW,
)
from jev_judge_mcp.providers import PRIMARY_CREDENTIAL, ProviderConfigError
from jev_judge_mcp.providers.resolver import KeySource, select_provider, typesafe_key
from jev_judge_mcp.redaction.values import Redactor
from jev_judge_mcp.registry import TOOL_NAMES
from jev_judge_mcp.settings import Settings, load_settings, unexpanded_references

_BARE_SERVER = "mcp__jev"
_UNSET = "(unset)"
_MAX_CLAUDE_SETTINGS_BYTES = 1_048_576

ALLOW_RULES: tuple[str, ...] = tuple(f"mcp__jev__{name}" for name in TOOL_NAMES)

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
_KNOWN_EXPLICIT = frozenset(PRIMARY_CREDENTIAL)


def main(argv: Sequence[str], *, home: Path | None = None, cwd: Path | None = None) -> int:
    """Print the configuration report. Exit 0 when a provider resolves, 1 when it does not, 2 on usage."""
    parser = argparse.ArgumentParser(
        prog="jev-judge-mcp doctor",
        description=(
            "Offline configuration check: the resolved provider, how it was chosen, the credential "
            "variable names that are set, the policy defaults, and the mcp__jev__* allow rules the "
            "Claude settings files already carry. Reads those files and writes nothing; calls no "
            "provider; prints no credential value."
        ),
    )
    try:
        parser.parse_args(list(argv))
    except SystemExit as parse_exit:
        # `--help` is a help page (exit 0), the same argparse shape `setup` and `install` print;
        # a stray argument is the usage error (exit 2).
        return int(parse_exit.code or 0)
    settings = load_settings()
    root = Path.home() if home is None else home
    work = Path.cwd() if cwd is None else cwd
    name, failure = _resolve(settings)
    report = _report(settings, root, work, name, failure)
    sys.stdout.write(Redactor(redaction_values(settings))(report))
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
    _, source = typesafe_key(settings)
    lines = [
        _line("provider", name if name is not None else _UNSET),
        _line("via", _via(settings, name, source)),
        _line("key", " ".join(keys) if keys else _UNSET),
        _line(
            "key file", f"{'present' if stored else 'absent'} at {stored_key_path(settings)}{_KEY_FILE_ROLE[source]}"
        ),
        *(
            _line("warning", f"{variable} holds an unexpanded reference and counts as unset")
            for variable in unexpanded_references()
        ),
    ]
    if failure is not None:
        lines.append(_line("error", failure))
    lines.append(_line("policy", " ".join(f"{label}={value}" for label, value in _POLICY)))
    lines.append(_line("version", POLICY_VERSION))
    lines.append(_line("allow", " ".join(ALLOW_RULES)))
    lines.append(_line("checked", ", ".join(str(path) for path in paths)))
    lines.append(_line("claude", _claude_line(paths, covered)))
    if missing:
        lines.append(_line("missing", " ".join(missing)))
        lines.append(_line("snippet", _snippet(missing)))
    return "".join(lines)


_KEY_FILE_ROLE: dict[KeySource, str] = {
    "environment": " (env wins)",
    "key_file": " (fills TYPESAFE_API_KEY)",
    "none": "",
}
"""What the key file is doing, from the source resolution reports (ADR-0046)."""


def _line(label: str, value: str) -> str:
    return f"{label:<8}: {value}\n"


def _resolve(settings: Settings) -> tuple[str | None, str | None]:
    """Provider name, or the configuration error, without opening a client."""
    try:
        provider, _ = select_provider(settings)
    except ProviderConfigError as error:
        return None, str(error)
    return provider, None


def _via(settings: Settings, name: str | None, source: KeySource) -> str:
    if name is None:
        return "unconfigured"
    explicit = settings.jev_provider.lower()
    if explicit == name and explicit in _KNOWN_EXPLICIT:
        return f"explicit: {name}"
    if name == "typesafe" and source == "key_file":
        return "auto: key file"
    return f"auto: {_primary_key(settings, name)} found"


def _primary_key(settings: Settings, name: str) -> str:
    if name == "cloudflare":
        return _cloudflare_token_name(settings) or PRIMARY_CREDENTIAL["cloudflare"]
    return PRIMARY_CREDENTIAL[name] if name in PRIMARY_CREDENTIAL else name


def _cloudflare_token_name(settings: Settings) -> str | None:
    if _filled(settings.jev_cloudflare_api_token):
        return "JEV_CLOUDFLARE_API_TOKEN"
    if _filled(settings.cloudflare_api_token):
        return "CLOUDFLARE_API_TOKEN"
    return None


def _set_names(settings: Settings) -> tuple[str, ...]:
    """Credential variable names that are non-empty. Values stay in `Settings`."""
    names = {name for name, _ in settings.named_secrets()}
    if settings.cloudflare_account_id:
        names.add("CLOUDFLARE_ACCOUNT_ID")
    return tuple(
        alias
        for field in Settings.model_fields.values()
        if isinstance(alias := field.validation_alias, str) and alias in names
    )


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
        data = read_bounded(path, max_bytes=_MAX_CLAUDE_SETTINGS_BYTES, follow=True)
        parsed = decode_json(data.decode("utf-8"))
    except (OSError, ValueError):
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
