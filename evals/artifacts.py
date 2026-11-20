"""Publish complete study artifacts without exposing partial writes."""

import json
import os
import tempfile
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import Any, cast

from jev_judge_mcp.fsutil import read_bounded

RESULT_BYTES_MAX = 8 * 1024 * 1024


def read_artifact(path: Path, *, max_bytes: int = RESULT_BYTES_MAX) -> bytes:
    """Read a regular study file up to max_bytes. Missing, symlinked, special or oversize files raise OSError."""
    return read_bounded(path, max_bytes=max_bytes)


def write_json(path: Path, payload: Mapping[str, Any]) -> None:
    content = json.dumps(payload, indent=2, allow_nan=False) + "\n"
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.", delete=False) as staging:
        temporary = Path(staging.name)
        try:
            staging.write(content.encode("utf-8"))
            staging.flush()
            os.fsync(staging.fileno())
            staging.close()
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)


def read_result(path: Path) -> dict[str, Any] | None:
    try:
        row: object = json.loads(read_artifact(path).decode("utf-8"))
    except (ValueError, OSError, RecursionError):
        return None
    return cast(dict[str, Any], row) if isinstance(row, dict) else None


def read_records(
    paths: Iterable[Path], *, required: tuple[str, ...], valid: Callable[[dict[str, Any]], bool]
) -> tuple[dict[Path, dict[str, Any]], list[Path]]:
    records: dict[Path, dict[str, Any]] = {}
    incomplete: list[Path] = []
    for path in sorted(paths):
        record = read_result(path)
        if (
            record is not None
            and all(isinstance(record.get(key), str) and record[key] for key in required)
            and record["run_id"] == path.parent.name
            and valid(record)
        ):
            records[path] = record
            continue
        incomplete.append(path)
    return records, incomplete


def incomplete_message(paths: Iterable[Path], root: Path) -> str:
    return "incomplete result records: " + ", ".join(str(path.parent.relative_to(root)) for path in paths)
