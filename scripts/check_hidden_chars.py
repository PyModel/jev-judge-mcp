"""Fail when a tracked file hides text: invisible format characters, bidi controls, or bytes that are not UTF-8.

    check_hidden_chars.py

Stdlib only. Exit 0 clean, 1 on a hit, 2 when git cannot list the tree. Every tracked regular
file is scanned; nothing is skipped as binary. A binary file joins BINARY by path when one is
first tracked (none is today).
"""

import subprocess
import sys
import unicodedata
from collections.abc import Iterable, Sequence
from pathlib import Path

BINARY: frozenset[str] = frozenset()
# Trojan Source controls are never legitimate in this tree.
BIDI_CONTROLS = frozenset(range(0x202A, 0x202F)) | frozenset(range(0x2066, 0x206A))
# Joiners shape emoji and Indic/Persian words; marks steer RTL text. Each is allowed only
# next to visible non-ASCII text, so a run of two or more hidden characters always fails.
# ponytail: neighbour heuristic; one joiner between two non-ASCII characters passes by design.
JOINERS = frozenset({0x200C, 0x200D})
DIRECTION_MARKS = frozenset({0x200E, 0x200F, 0x061C})
EMOJI_SELECTORS = frozenset({0xFE0E, 0xFE0F})
FILLERS = frozenset({0x0000, 0x034F, 0x115F, 0x1160, 0x17B4, 0x17B5, 0x3164, 0xFFA0})
SELECTOR_SUPPLEMENT = range(0xE0100, 0xE01F0)


def _shaping_neighbour(char: str) -> bool:
    return ord(char) > 0x7F and unicodedata.category(char) not in {"Cf", "Zs", "Cc"}


def _hidden(text: str, index: int) -> bool:
    char = text[index]
    code = ord(char)
    if code == 0xFEFF:
        return index != 0  # a leading BOM is an encoding mark, not content
    if code in BIDI_CONTROLS or code in FILLERS or code in SELECTOR_SUPPLEMENT:
        return True
    before = text[index - 1] if index > 0 else ""
    after = text[index + 1] if index + 1 < len(text) else ""
    if code in JOINERS:
        return not (before and after and _shaping_neighbour(before) and _shaping_neighbour(after))
    if code in DIRECTION_MARKS:
        return not any(c and unicodedata.bidirectional(c) in {"R", "AL"} for c in (before, after))
    if code in EMOJI_SELECTORS:
        return not (before and _shaping_neighbour(before))
    # Tag characters (U+E0000 block), soft hyphen, word joiners, and every other format character.
    return unicodedata.category(char) == "Cf"


def offending(text: str) -> list[tuple[int, int, int]]:
    """(line, column, codepoint) for each hidden character. Columns are 1-based code points."""
    found: list[tuple[int, int, int]] = []
    line, column = 1, 1
    for index, char in enumerate(text):
        if _hidden(text, index):
            found.append((line, column, ord(char)))
        if char == "\n":
            line, column = line + 1, 1
        else:
            column += 1
    return found


def scan(files: Iterable[tuple[str, bytes]]) -> list[str]:
    """Error lines for every tracked file that hides text or is not UTF-8."""
    errors: list[str] = []
    for path, data in files:
        if path in BINARY:
            continue
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as error:
            errors.append(f"{path}: not UTF-8 at byte {error.start}")
            continue
        errors.extend(f"{path}:{line}:{column}: U+{code:04X}" for line, column, code in offending(text))
    return errors


def tracked(root: Path) -> list[tuple[str, bytes]]:
    listed = subprocess.run(["git", "ls-files", "-z"], cwd=root, check=True, capture_output=True)  # noqa: S607
    files: list[tuple[str, bytes]] = []
    for raw in listed.stdout.split(b"\0"):
        if not raw:
            continue
        path = raw.decode("utf-8")
        file = root / path
        if file.is_file() and not file.is_symlink():
            files.append((path, file.read_bytes()))
    return files


def main(argv: Sequence[str] | None = None) -> int:
    if argv:
        print(__doc__, file=sys.stderr)
        return 2
    try:
        top = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],  # noqa: S607
            check=True,
            capture_output=True,
            text=True,
        )
        files = tracked(Path(top.stdout.strip()))
    except (OSError, subprocess.CalledProcessError) as error:
        print(f"hidden character check could not list the tree: {error}", file=sys.stderr)
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
