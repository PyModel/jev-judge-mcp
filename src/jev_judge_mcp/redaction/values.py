"""Exact-value redaction and process logging (ADR-0008, ADR-0050)."""

import json
import logging
import re
import sys
from collections.abc import Iterable
from threading import Lock
from urllib.parse import urlsplit

from jev_judge_mcp.settings import LogLevel, Settings, unexpanded_references

REDACTED = "[redacted]"
DEFAULT_PORTS = {"http": 80, "https": 443}
_log_redaction_lock = Lock()


class Redactor:
    """Replaces every configured secret with `[redacted]` in MCP-visible text and log lines (ADR-0008).

    Replacement is of the exact value, so `Bearer <secret>` is covered. A secret that is a URL with
    userinfo also contributes its userinfo and password: an HTTP client may print the URL
    normalized (lowercased host, added `/`), and the credential must not survive that. A URL-shaped
    secret also contributes its normalized whole form, for the same reason.
    """

    def __init__(self, secrets: Iterable[str]) -> None:
        self.secrets = frozenset(secrets)
        values = {value for secret in self.secrets for value in variants(secret)}
        spellings = sorted(values, key=lambda value: (-len(value), value))
        self._pattern = re.compile("|".join(map(re.escape, spellings))) if spellings else None

    def __call__(self, text: str) -> str:
        return self._pattern.sub(REDACTED, text) if self._pattern is not None else text


def variants(secret: str) -> frozenset[str]:
    values = {value for value in (secret, *_userinfo_parts(secret), _normalized_url(secret)) if value}
    return frozenset(values | {json.dumps(value, ensure_ascii=False)[1:-1] for value in values})


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
    default = DEFAULT_PORTS.get(parts.scheme.lower())
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

    def extend(self, values: Iterable[str]) -> None:
        new_values = self._redact.secrets.union(values)
        if new_values != self._redact.secrets:
            self._redact = Redactor(new_values)

    def filter(self, record: logging.LogRecord) -> bool:
        redact = self._redact
        try:
            message = record.getMessage()
        except Exception:
            try:
                message = f"{record.msg!s} {record.args!r}"
            except Exception:
                message = "<unformattable log message>"
        record.msg = redact(message)
        record.args = None
        if record.exc_info is not None:
            # Render the traceback now, redacted, so the formatter never sees the raw exception.
            record.exc_text = redact(logging.Formatter().formatException(record.exc_info))
            record.exc_info = None
        elif record.exc_text:
            record.exc_text = redact(record.exc_text)
        if record.stack_info:
            record.stack_info = redact(record.stack_info)
        return True


def configure_process_logging(settings: Settings, redact: Redactor) -> None:
    configure_logging(settings.log_level, redact.secrets)
    for variable in unexpanded_references():
        logging.getLogger("jev_judge_mcp").warning("%s holds an unexpanded reference and counts as unset", variable)


def configure_logging(level: LogLevel, secrets: Iterable[str] = ()) -> None:
    """Log to stderr. Every record, from any logger, is redacted of `secrets` first (ADR-0008)."""
    handler = logging.StreamHandler(sys.stderr)
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    handler.addFilter(RedactingFilter(Redactor(secrets)))
    root = logging.getLogger()
    with _log_redaction_lock:
        root.handlers[:] = [handler]
        root.setLevel(level)


def register_log_secrets(redact: Redactor) -> None:
    """Extend the installed log filters before the newly resolved credential is used."""
    with _log_redaction_lock:
        for handler in logging.getLogger().handlers:
            for log_filter in handler.filters:
                if isinstance(log_filter, RedactingFilter):
                    log_filter.extend(redact.secrets)
