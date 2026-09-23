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


def stored_key(settings: Settings) -> str:
    """The stored key, or `""`. Missing, unreadable, or blank stores as empty — never an error."""
    try:
        # utf-8-sig drops a leading BOM when present and is otherwise plain UTF-8.
        value = stored_key_path(settings).read_text(encoding="utf-8-sig").strip()
    except OSError:
        return ""
    return value


def store_key(settings: Settings, api_key: str) -> Path:
    """Write `api_key` for later runs. The directory is 0700, the file 0600, the write atomic."""
    path = fsutil.write_private_atomic(stored_key_path(settings), api_key.strip() + "\n")
    logger.info("stored the TypeSafe API key at %s", path)
    return path
