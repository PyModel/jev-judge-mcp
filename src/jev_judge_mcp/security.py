"""The per-call key-store exclusion and provider-bound redaction (ADR-0087)."""

from dataclasses import dataclass
from pathlib import Path

from jev_judge_mcp import keyfile
from jev_judge_mcp.redaction.literals import redact_credential_literals
from jev_judge_mcp.redaction.values import Redactor, variants
from jev_judge_mcp.settings import Settings

MIN_SECRET_LENGTH = 8


@dataclass(frozen=True, slots=True)
class SecurityGuard:
    key_path: Path
    redact: Redactor

    def scrub(self, text: str) -> str:
        return redact_credential_literals(self.redact(text))


def build_guard(settings: Settings) -> SecurityGuard:
    return SecurityGuard(keyfile.stored_key_path(settings), checked_redactor(settings, keyfile.stored_key(settings)))


def checked_redactor(settings: Settings, stored: str) -> Redactor:
    named = keyfile.named_redaction_values(settings, stored)
    for source, secret in named:
        if any(len(value) < MIN_SECRET_LENGTH for value in variants(secret)):
            raise ValueError(
                f"{source} has a secret shorter than {MIN_SECRET_LENGTH} characters; refusing to start: "
                "a secret that short cannot be redacted without corrupting text"
            )
    return Redactor(secret for _, secret in named)
