"""Strict credential-literal detection for written file content.

A hit denies a Write or Edit outright (ADR-0076), so the detector is high precision by
design: ordinary code must never match. Hits are well-known token formats and a quoted
high-entropy literal assigned to a secret-named key. A bare identifier, a type
annotation, a function name, or a reference such as ``os.environ[...]``,
``settings.x``, or an f-string placeholder is never a hit.

This is not :mod:`jev_judge_mcp.redact_action`: that module redacts shell commands,
where credential-shaped substrings are the risk, and it rewrites ordinary code. Here the
judged text is file content, and mangling it would misjudge the write.
"""

import re
from collections.abc import Iterator

REDACTED = "[redacted]"
"""The same harness-facing marker ``redact_action`` uses; defined here so this module stays separate."""

_FORMATS: tuple[re.Pattern[str], ...] = (
    # AWS access key ids.
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    # GitHub tokens.
    re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,})\b"),
    # Slack tokens.
    re.compile(r"\bxox[abdeporsu]-[A-Za-z0-9-]{10,}\b"),
    # OpenAI- and Anthropic-style keys.
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),
    # PEM private-key blocks.
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    # JWTs: three base64url segments; the first two start eyJ, the base64 of '{"'.
    re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"),
)

_ASSIGNMENT = re.compile(
    r"\b(?:password|passwd|secret|token|api_?key|access_?key|private_?key|credential|auth)[\w-]*"
    r"[\"']?\s*[:=]\s*"
    # The opening quote of the value. An f-string prefix never opens the literal.
    r"(?<![fF])([\"'])",
    re.IGNORECASE,
)


def _high_entropy(value: str) -> bool:
    """Long enough and drawn from enough character classes to be material, not prose or a name."""
    if len(value) < 16 or "{" in value or "}" in value:
        return False
    classes = sum(
        (
            any(c.islower() for c in value),
            any(c.isupper() for c in value),
            any(c.isdigit() for c in value),
            any(not c.isalnum() for c in value),
        )
    )
    return classes >= 3


def _literals(text: str) -> Iterator[tuple[int, int]]:
    for pattern in _FORMATS:
        for match in pattern.finditer(text):
            yield match.span()
    for match in _ASSIGNMENT.finditer(text):
        quote = match.group(1)
        if quote is None:
            continue
        end = text.find(quote, match.end())
        if end != -1 and _high_entropy(text[match.end() : end]):
            # Only the quoted value is the literal; the secret-named key stays readable.
            yield match.end() - 1, end + 1


def has_credential_literal(text: str) -> bool:
    """Whether the text carries a credential literal: a known token format or an assigned
    quoted high-entropy value."""
    return next(_literals(text), None) is not None


def redact_credential_literals(text: str) -> str:
    """The text with every credential literal replaced by ``REDACTED``.

    On text without a literal this is the identity, so written code reads naturally to the
    judge; that is the property :func:`has_credential_literal` guarantees for any text that
    reaches this point on the Write/Edit path.
    """
    out: list[str] = []
    cursor = 0
    for start, stop in sorted(_literals(text)):
        if start < cursor:
            continue
        out.append(text[cursor:start])
        out.append(REDACTED)
        cursor = stop
    out.append(text[cursor:])
    return "".join(out)
