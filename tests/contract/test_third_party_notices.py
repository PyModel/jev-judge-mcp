"""THIRD_PARTY_NOTICES.md names every tracked file that carries the reference's tool text.

The expected set is derived from the tracked tools-list snapshot, not from a copied inventory.
Question text and resolver strings have no snapshot in the tree, so those carriers stay in the
notice itself.
"""

import json
import re
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
REFERENCE = "docs/reference/ts-0.5.0-tools-list.json"


def _descriptions(node: Any) -> Iterator[str]:
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "description" and isinstance(value, str) and len(value) >= 50:
                yield value
            yield from _descriptions(value)
    elif isinstance(node, list):
        for item in node:
            yield from _descriptions(item)


def test_notice_names_every_file_carrying_reference_tool_text() -> None:
    notice = (ROOT / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
    section = notice.split("## Archify")[0]
    named = set(re.findall(r"^- .*?`([^`]+)`", section, re.MULTILINE))
    prefixes = tuple(path for path in named if path.endswith("/"))
    carriers: set[str] = set()
    for text in _descriptions(json.loads((ROOT / REFERENCE).read_text(encoding="utf-8"))):
        # A 40-character middle slice survives the line wrapping of Python string literals.
        found = subprocess.run(
            ["git", "grep", "-l", "-F", text[10:50]],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        ).stdout.split()
        carriers.update(found)
    missing = sorted(path for path in carriers if path not in named and not path.startswith(prefixes))
    assert missing == []
