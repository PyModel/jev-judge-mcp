"""Fail when actionlint reports a finding in .github/workflows/.

    check_workflows.py

Checks workflow and composite action references even without actionlint. Runs actionlint over the
repository's workflows, with its shellcheck integration when shellcheck is installed too.
actionlint is not a Python dependency; CI installs it through
scripts/ci/install_actionlint.sh (the pin the CI image shares) and the pre-push gate's Linux
image ships it (docker/ci-linux.Dockerfile), so both enforcement points run it for real. A
local run without the binary skips with one stderr line — a skip, not a proof.

Exit 0 clean or skipped, 1 on findings, 2 when the repository or actionlint cannot be run.
"""

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

TOOL = "actionlint"

_KEY = re.compile(r"""(?P<quote>['"]?)(?P<name>[\w.-]+)(?P=quote)\s*:(?:\s+|$)(?P<value>.*)""", re.ASCII)
_REF = re.compile(
    r"(?:\./[\w./-]+|docker://[\w./:-]+@sha256:[a-fA-F0-9]{64}|[\w.-]+/[\w.-]+(?:/[\w.-]+)*@[a-fA-F0-9]{40})",
    re.ASCII,
)
_SIMPLE_LIST = re.compile(r"\[\s*(?:[\w./-]+(?:\s*,\s*[\w./-]+)*)?\s*\]", re.ASCII)


def _pin_error(path: Path) -> int | None:
    """Accept ordinary block mappings. Reject indirect keys, anchors and complex flow collections."""
    scalar_indent: int | None = None
    for number, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        content = line.lstrip(" ")
        indent = len(line) - len(content)
        if not content or content.startswith("#"):
            continue
        if scalar_indent is not None and indent > scalar_indent:
            continue
        scalar_indent = None
        content = re.split(r"\s+#", content, maxsplit=1)[0].rstrip()
        if content == "---":
            continue
        sequence = content.startswith("- ")
        if sequence:
            content = content[2:].lstrip()
            indent = line.index(content, indent + 2)
        key = _KEY.fullmatch(content)
        if key is None:
            if sequence and re.fullmatch(r"[\w./-]+", content):
                continue
            return number
        value = key["value"].strip()
        if key["name"].lower() == "uses":
            if len(value) > 1 and value[0] in ("'", '"') and value[-1] == value[0]:
                value = value[1:-1]
            if not _REF.fullmatch(value) or (value.startswith("./") and ".." in value.split("/")):
                return number
        elif re.fullmatch(r"[|>][+-]?", value):
            scalar_indent = indent
        elif value.startswith(("&", "*", "!", "{", "[")) and value != "{}" and not _SIMPLE_LIST.fullmatch(value):
            return number
    return None


def _action_files(root: Path) -> list[Path]:
    files: list[Path] = []

    def unreadable(error: OSError) -> None:
        raise error

    for directory, children, names in os.walk(root, onerror=unreadable):
        if ".git" in children:
            children.remove(".git")
        files.extend(Path(directory) / name for name in names if name in {"action.yml", "action.yaml"})
    return files


def main(argv: list[str] | None = None) -> int:
    if argv:
        print(__doc__, file=sys.stderr)
        return 2
    root = Path(__file__).resolve().parent.parent
    if not (root / ".github" / "workflows").is_dir():
        print("workflow lint could not find .github/workflows/", file=sys.stderr)
        return 2
    directory = root / ".github/workflows"
    workflows = sorted(p for p in directory.iterdir() if p.suffix in {".yml", ".yaml"})
    try:
        for path in [*workflows, *_action_files(root)]:
            if (line := _pin_error(path)) is not None:
                print(f"{path.relative_to(root)}:{line}: unsupported action reference or YAML syntax", file=sys.stderr)
                return 1
    except (OSError, UnicodeError) as error:
        print(f"workflow lint could not read action references: {error}", file=sys.stderr)
        return 2
    if shutil.which(TOOL) is None:
        print(f"workflow lint skipped: {TOOL} is not installed (the Linux gate leg ships it)", file=sys.stderr)
        return 0
    try:
        proc = subprocess.run([TOOL, *workflows], cwd=root, check=False, capture_output=True, text=True)  # noqa: S603
    except OSError as error:
        print(f"workflow lint could not run {TOOL}: {error}", file=sys.stderr)
        return 2
    if proc.returncode == 0:
        print("workflow lint passed")
        return 0
    if proc.returncode == 1:
        print("workflow lint failed:", file=sys.stderr)
        print(proc.stdout, end="", file=sys.stderr)
        print(proc.stderr, end="", file=sys.stderr)
        return 1
    print(f"workflow lint could not run {TOOL} (exit {proc.returncode}):", file=sys.stderr)
    print(proc.stderr, end="", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
