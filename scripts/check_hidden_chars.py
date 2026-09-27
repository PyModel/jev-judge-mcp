"""Fail when a tracked text file contains a zero-width or bidi control character.

    check_hidden_chars.py

Stdlib only. Exit 1 on a hit, 2 if git cannot list the tree. An allow-list entry is a
repo-relative path whose fixture must contain the raw character; an escape in source
does not need one. The list is empty unless a fixture cannot say the character any
other way.
"""

import subprocess
import sys
from collections.abc import Iterable, Sequence
from pathlib import Path

# Zero-width format characters, plus bidi controls (Trojan Source: embeddings,
# overrides, isolates, and directional marks).
FORBIDDEN = frozenset(
    {
        0x061C,  # Arabic letter mark
        0x180E,  # Mongolian vowel separator
        0x200B,
        0x200C,
        0x200D,
        0x200E,
        0x200F,
        0x202A,
        0x202B,
        0x202C,
        0x202D,
        0x202E,
        0x2060,
        0x2061,
        0x2062,
        0x2063,
        0x2064,
        0x2066,
        0x2067,
        0x2068,
        0x2069,
        0x206A,
        0x206B,
        0x206C,
        0x206D,
        0x206E,
        0x206F,
        0xFEFF,
    }
)
ALLOW: frozenset[str] = frozenset()


def offending(text: str) -> list[tuple[int, int, int]]:
    """(line, column, codepoint) for each forbidden character. Columns are 1-based."""
    found: list[tuple[int, int, int]] = []
    line = 1
    column = 1
    for char in text:
        code = ord(char)
        if code in FORBIDDEN:
            found.append((line, column, code))
        if char == "\n":
            line += 1
            column = 1
        else:
            column += 1
    return found


def scan(files: Iterable[tuple[str, str]], allow: frozenset[str] = ALLOW) -> list[str]:
    """Error lines for files that are not allow-listed and contain a forbidden character."""
    errors: list[str] = []
    for path, text in files:
        if path in allow:
            continue
        for line, column, code in offending(text):
            errors.append(f"{path}:{line}:{column}: U+{code:04X}")
    return errors


def tracked_text(root: Path) -> list[tuple[str, str]]:
    listed = subprocess.run(
        ["git", "ls-files", "-z"],  # noqa: S607
        cwd=root,
        check=True,
        capture_output=True,
    )
    files: list[tuple[str, str]] = []
    for raw in listed.stdout.split(b"\0"):
        if not raw:
            continue
        path = raw.decode("utf-8")
        file = root / path
        if not file.is_file():
            continue
        data = file.read_bytes()
        if b"\0" in data:
            continue
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            continue
        files.append((path, text))
    return files


def main(argv: Sequence[str] | None = None) -> int:
    if argv:
        print(__doc__, file=sys.stderr)
        return 2
    top = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],  # noqa: S607
        check=True,
        capture_output=True,
        text=True,
    )
    root = Path(top.stdout.strip())
    try:
        files = tracked_text(root)
    except subprocess.CalledProcessError as error:
        print(f"hidden character check failed: {error}", file=sys.stderr)
        return 2
    errors = scan(files)
    if errors:
        print("hidden character check failed:", file=sys.stderr)
        print("\n".join(errors), file=sys.stderr)
        return 1
    print("hidden character check passed")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
