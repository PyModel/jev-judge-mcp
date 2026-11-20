"""Redact key material before it reaches stdout, a diff, or the state file."""

import json
import re
from collections.abc import Iterable, Mapping

from pydantic import ValidationError

from jev_judge_mcp.domain import is_json_array, is_json_object
from jev_judge_mcp.keyfile import redaction_values
from jev_judge_mcp.redaction.literals import redact_credential_literals
from jev_judge_mcp.redaction.values import REDACTED, Redactor
from jev_judge_mcp.settings import load_settings

HASH_PLACEHOLDER = "<redacted>"
_KEY_NAME = "TYPESAFE_API_KEY"

# A literal JSON or TOML string assigned to TYPESAFE_API_KEY, excluding the reference forms
# terminal agents store (`${TYPESAFE_API_KEY}`, `{env:TYPESAFE_API_KEY}`). TOML spells the key
# bare or quoted and the value as a basic ("...") or literal ('...') string; every form is redacted.
_JSON_LITERAL = re.compile(r'("TYPESAFE_API_KEY"\s*:\s*")(?!\$\{|\{env:)((?:\\.|[^"\\])*)(")')
_TOML_KEY = r"(?:\"TYPESAFE_API_KEY\"|'TYPESAFE_API_KEY'|TYPESAFE_API_KEY)"
_TOML_LITERAL = re.compile(rf"({_TOML_KEY}\s*=\s*\")(?!\$\{{|\{{env:)((?:\\.|[^\"\\])*)(\")")
_TOML_LITERAL_SINGLE = re.compile(rf"({_TOML_KEY}\s*=\s*')(?!\$\{{|\{{env:)([^']*)(')")


def is_reference(value: str) -> bool:
    """True when the value tells the agent to read the key from the environment."""
    return value.startswith("${") or value.startswith("{env:") or value == _KEY_NAME


def redact_text(text: str, secrets: Iterable[str]) -> str:
    """Replace known secrets, then any remaining literal key assignment."""
    text = Redactor(secrets)(text)
    text = _JSON_LITERAL.sub(rf"\1{REDACTED}\3", text)
    text = _TOML_LITERAL.sub(rf"\1{REDACTED}\3", text)
    return _TOML_LITERAL_SINGLE.sub(rf"\1{REDACTED}\3", text)


def redact_diagnostic(text: str, secrets: Iterable[str]) -> str:
    try:
        inventory = redaction_values(load_settings())
    except ValidationError:
        return "[diagnostic omitted: could not load secret inventory]"
    return redact_credential_literals(redact_text(text, [*inventory, *secrets]))


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
