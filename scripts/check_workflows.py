"""Fail when actionlint reports a finding in .github/workflows/.

    check_workflows.py

Both Actionlint and ShellCheck must be installed. A stalled native check retries once with
the pinned upstream pipe fix if Go is available. Missing tools and timeouts fail the gate.
Exit 0 on success, 1 on lint findings, 2 when lint could not complete.
"""

import os
import re
import shutil
import signal
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

TOOL = "actionlint"
# v1.7.12 writes to the stdin pipe before starting ShellCheck. Upstream fd33e9f starts the reader first.
FIXED_TOOL = "github.com/rhysd/actionlint/cmd/actionlint@v1.7.13-0.20260417151945-fd33e9f582a0"

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


def _bounded(
    command: list[str], root: Path, timeout: float, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    with subprocess.Popen(  # noqa: S603
        command,
        cwd=root,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=os.name == "posix",
    ) as process:
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            if os.name == "posix":
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass  # It exited between the deadline and the signal.
            else:
                process.kill()
            process.wait()
            raise
    return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)


def _lint(root: Path, workflows: list[Path], timeout: float) -> subprocess.CompletedProcess[str]:
    args = [str(path) for path in workflows]
    try:
        return _bounded([TOOL, *args], root, timeout)
    except subprocess.TimeoutExpired:
        print(f"workflow lint: {TOOL} with ShellCheck timed out after {timeout:g}s", file=sys.stderr)
    go = shutil.which("go")
    if go is None:
        raise OSError(f"install {FIXED_TOOL} with Go or put a fixed actionlint on PATH")
    print("Retrying with the pinned upstream ShellCheck pipe fix", file=sys.stderr)
    with TemporaryDirectory() as directory:
        build = _bounded([go, "install", FIXED_TOOL], root, 180, {**os.environ, "GOBIN": directory, "GOWORK": "off"})
        if build.returncode:
            raise OSError(f"could not build pinned actionlint: {build.stderr.strip()}")
        return _bounded([str(Path(directory) / ("actionlint.exe" if os.name == "nt" else TOOL)), *args], root, timeout)


def main(argv: list[str] | None = None, *, timeout: float = 30) -> int:
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
    for required in (TOOL, "shellcheck"):
        if shutil.which(required) is None:
            print(f"workflow lint requires {required} on PATH", file=sys.stderr)
            return 2
    try:
        proc = _lint(root, workflows, timeout)
    except (OSError, subprocess.TimeoutExpired) as error:
        print(f"workflow lint could not run {TOOL} with ShellCheck: {error}", file=sys.stderr)
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
