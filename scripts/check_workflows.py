"""Fail when actionlint reports a finding in .github/workflows/.

    check_workflows.py

Runs actionlint over the repository's workflow files, with its shellcheck integration when
shellcheck is installed too. actionlint is not a Python dependency; CI installs it through
scripts/ci/install_actionlint.sh (the pin the CI image shares) and the pre-push gate's Linux
image ships it (docker/ci-linux.Dockerfile), so both enforcement points run it for real. A
local run without the binary skips with one stderr line — a skip, not a proof.

Exit 0 clean or skipped, 1 on findings, 2 when the repository or actionlint cannot be run.
"""

import shutil
import subprocess
import sys
from pathlib import Path

TOOL = "actionlint"


def main(argv: list[str] | None = None) -> int:
    if argv:
        print(__doc__, file=sys.stderr)
        return 2
    root = Path(__file__).resolve().parent.parent
    if not (root / ".github" / "workflows").is_dir():
        print("workflow lint could not find .github/workflows/", file=sys.stderr)
        return 2
    if shutil.which(TOOL) is None:
        print(f"workflow lint skipped: {TOOL} is not installed (the Linux gate leg ships it)", file=sys.stderr)
        return 0
    try:
        proc = subprocess.run([TOOL], cwd=root, check=False, capture_output=True, text=True)  # noqa: S603
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
