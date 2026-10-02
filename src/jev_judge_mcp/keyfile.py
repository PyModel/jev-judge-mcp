"""The key file `jev-judge-mcp setup` writes and the resolver falls back to (ADR-0046).

`TYPESAFE_API_KEY` in the environment always wins; the file is the fallback for processes that
cannot be restarted with an export. The write goes through `fsutil` — atomic, mode 0600 inside a
0700 directory — and the read is `utf-8-sig` so a byte-order mark another tool left behind cannot
become part of the key. Nothing here ever logs the value.
"""

import logging
from pathlib import Path

from jev_judge_mcp import fsutil
from jev_judge_mcp.settings import Settings

logger = logging.getLogger("jev_judge_mcp.keyfile")


def stored_key_path(settings: Settings) -> Path:
    """Where the stored key lives: `JEV_MCP_KEY_FILE`, else the XDG config default."""
    if settings.key_file is not None:
        return settings.key_file
    return fsutil.xdg_home("config") / "jev-mcp" / "key"


KEY_FILE_BYTES_MAX = 4096
"""Bytes read from the key file at most. A key is a short token; a store larger than this (a wrong
path, a device, a dump) is not a key and reads as empty, never as a hang or a traceback."""


def stored_key(settings: Settings) -> str:
    """The stored key, or `""`. Missing, unreadable, undecodable, oversized, or blank stores as
    empty — never an error."""
    try:
        with stored_key_path(settings).open("rb") as handle:
            data = handle.read(KEY_FILE_BYTES_MAX + 1)
        if len(data) > KEY_FILE_BYTES_MAX:
            return ""
        # utf-8-sig drops a leading BOM when present and is otherwise plain UTF-8.
        return data.decode("utf-8-sig").strip()
    except (OSError, UnicodeDecodeError):
        return ""


def redaction_values(settings: Settings) -> list[str]:
    """Every value redaction must cover: the configured secrets plus the stored key (ADR-0017, ADR-0046).

    One owner for the set, so the provider redactor, the log filter, the hooks, and the ask
    tool's output scrub cannot disagree about what a secret is.
    """
    stored = stored_key(settings)
    values = settings.secret_values()
    return [*values, stored] if stored else values


def store_key(settings: Settings, api_key: str) -> Path:
    """Write `api_key` for later runs. The directory is 0700, the file 0600, the write atomic."""
    path = fsutil.write_private_atomic(stored_key_path(settings), api_key.strip() + "\n")
    logger.info("stored the TypeSafe API key at %s", path)
    return path
