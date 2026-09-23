"""Redact key material before it reaches stdout, a diff, or the state file."""

import json
import re
from collections.abc import Iterable, Mapping

from jev_judge_mcp.domain import is_json_object
from jev_judge_mcp.install.values import is_json_array

REDACTED = "[redacted]"
HASH_PLACEHOLDER = "<redacted>"
_KEY_NAME = "TYPESAFE_API_KEY"

# A literal JSON or TOML string assigned to TYPESAFE_API_KEY, excluding the reference forms
# terminal agents store (`${TYPESAFE_API_KEY}`, `{env:TYPESAFE_API_KEY}`).
_JSON_LITERAL = re.compile(r'("TYPESAFE_API_KEY"\s*:\s*")(?!\$\{|\{env:)((?:\\.|[^"\\])*)(")')
_TOML_LITERAL = re.compile(r"(TYPESAFE_API_KEY\s*=\s*\")(?!\$\{|\{env:)((?:\\.|[^\"\\])*)(\")")


def is_reference(value: str) -> bool:
    """True when the value tells the agent to read the key from the environment."""
    return value.startswith("${") or value.startswith("{env:") or value == _KEY_NAME


def redact_text(text: str, secrets: Iterable[str]) -> str:
    """Replace known secrets, then any remaining literal key assignment."""
    for secret in sorted({secret for secret in secrets if secret}, key=len, reverse=True):
        text = text.replace(secret, REDACTED)
    text = _JSON_LITERAL.sub(rf"\1{REDACTED}\3", text)
    return _TOML_LITERAL.sub(rf"\1{REDACTED}\3", text)


def redact_tree(value: object) -> object:
    """Copy of an entry whose literal key values are a fixed placeholder.

    The placeholder is what the state file hashes, so the file never stores the key.
    Reference forms are kept: they are not secret.
    """
    return _walk(value, None)


def _walk(value: object, key: str | None) -> object:
    if is_json_object(value):
        return {name: _walk(item, name) for name, item in value.items()}
    if is_json_array(value):
        return [_walk(item, None) for item in value]
    if isinstance(value, str) and key == _KEY_NAME and not is_reference(value):
        return HASH_PLACEHOLDER
    return value


def holds_literal(value: object) -> bool:
    """True when a TYPESAFE_API_KEY field stores the key itself."""
    return _found_literal(value, None)


def _found_literal(value: object, key: str | None) -> bool:
    if is_json_object(value):
        return any(_found_literal(item, name) for name, item in value.items())
    if is_json_array(value):
        return any(_found_literal(item, None) for item in value)
    return isinstance(value, str) and key == _KEY_NAME and not is_reference(value)


def canonical_hash_payload(entry: Mapping[str, object]) -> str:
    """Stable JSON of the redacted entry. Callers hash this; they do not store it raw in logs."""
    return json.dumps(redact_tree(entry), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
