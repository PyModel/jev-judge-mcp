"""Error types and handler error text, and secret redaction (ADR-0008)."""

import logging
from collections.abc import Iterable
from urllib.parse import urlsplit

REDACTED = "[redacted]"


class Redactor:
    """Replaces every configured secret with `[redacted]` in MCP-visible text and log lines (ADR-0008).

    Replacement is of the exact value, so `Bearer <secret>` is covered. A secret that is a URL with
    userinfo also contributes its userinfo and password: an HTTP client may print the URL
    normalized (lowercased host, added `/`), and the credential must not survive that. A URL-shaped
    secret also contributes its normalized whole form, for the same reason.
    """

    def __init__(self, secrets: Iterable[str]) -> None:
        values: set[str] = set()
        for secret in secrets:
            values.update(value for value in (secret, *_userinfo_parts(secret), _normalized_url(secret)) if value)
        # Longest first, so a secret containing another is replaced whole.
        self._secrets = sorted(values, key=len, reverse=True)

    def __call__(self, text: str) -> str:
        for secret in self._secrets:
            text = text.replace(secret, REDACTED)
        return text


def _userinfo_parts(secret: str) -> tuple[str, ...]:
    try:
        parts = urlsplit(secret)
    except ValueError:
        return ()
    if not parts.scheme or "@" not in parts.netloc:
        return ()
    userinfo = parts.netloc.rpartition("@")[0]
    password = userinfo.partition(":")[2]
    return (userinfo, password)


def _normalized_url(secret: str) -> str | None:
    """The URL as an HTTP client logs it: lowercased scheme and host, path added, default port gone.

    `None` when the value is not URL-shaped, and when normalization changes nothing.
    """
    if "://" not in secret:
        return None
    try:
        parts = urlsplit(secret)
        host = parts.hostname
        port = parts.port
    except ValueError:
        return None
    if not parts.scheme or not host:
        return None
    default = {"http": 80, "https": 443}.get(parts.scheme.lower())
    authority = host.lower() + (f":{port}" if port is not None and port != default else "")
    userinfo, at, _ = parts.netloc.rpartition("@")
    if at:
        authority = f"{userinfo}@{authority}"
    path = parts.path or "/"
    query = f"?{parts.query}" if parts.query else ""
    normalized = f"{parts.scheme.lower()}://{authority}{path}{query}"
    return normalized if normalized != secret else None


class RedactingFilter(logging.Filter):
    """A handler filter that redacts the fully formatted message, including exception text."""

    def __init__(self, redact: Redactor) -> None:
        super().__init__()
        self._redact = redact

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = self._redact(record.getMessage())
        record.args = None
        if record.exc_info is not None:
            # Render the traceback now, redacted, so the formatter never sees the raw exception.
            record.exc_text = self._redact(logging.Formatter().formatException(record.exc_info))
            record.exc_info = None
        elif record.exc_text:
            record.exc_text = self._redact(record.exc_text)
        if record.stack_info:
            record.stack_info = self._redact(record.stack_info)
        return True
