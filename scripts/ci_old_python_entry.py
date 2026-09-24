"""CI guard (ADR-0053): an installer-written entry starts even when the first Python is too old.

    python scripts/ci_old_python_entry.py

Stdlib only, and expected to run on that too-old interpreter: the CI smoke job runs it right
after `actions/setup-python` has put Python 3.10 first on PATH. Without the written `--python`
request, `uvx` resolves jev-judge-mcp against that interpreter and the entry cannot start
(dogfood finding F2: "the current Python version (3.10.19) does not satisfy Python>=3.12").

The guard builds the wheel, runs the old plain form first and requires it to fail (proving the
old interpreter really is the first one uv discovers), then installs through the installer
exactly as a user would and requires the installer's own post-write verify handshake to pass
against the written entry. A managed Python download is expected: that is the fix working.

Exits 0 when the guard holds, 1 when it does not.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from email.parser import HeaderParser
from pathlib import Path

DISTRIBUTION = "jev-judge-mcp"
ENTRY_NAME = "jev"
# Installer probes never touch a real profile: a throwaway HOME plus these unset (ADR-0053).
ISOLATED_ENV_VARS = (
    "XDG_CONFIG_HOME",
    "XDG_STATE_HOME",
    "OMP_PROFILE",
    "PI_PROFILE",
    "CLAUDE_CONFIG_DIR",
    "CODEX_HOME",
    "PI_CODING_AGENT_DIR",
    "PYTHINKER_CODE_HOME",
)


class GuardError(Exception):
    pass


def wheel_facts(wheel: Path) -> tuple[str, str]:
    """The wheel's (version, Requires-Python) from its top-level METADATA member."""
    with zipfile.ZipFile(wheel) as archive:
        matches = [name for name in archive.namelist() if name.count("/") == 1 and name.endswith(".dist-info/METADATA")]
        if len(matches) != 1:
            raise GuardError(f"{wheel.name} has {len(matches)} top-level METADATA members, expected 1")
        headers = HeaderParser().parsestr(archive.read(matches[0]).decode("utf-8"))
    version = headers["Version"]
    requires_python = headers["Requires-Python"]
    if not version or not requires_python:
        raise GuardError(f"{wheel.name} METADATA lacks Version or Requires-Python")
    return version, requires_python


def run(command: list[str], env: dict[str, str], timeout: float) -> subprocess.CompletedProcess[str]:
    print(f"$ {' '.join(command)}")
    completed = subprocess.run(command, capture_output=True, text=True, env=env, timeout=timeout)
    if completed.stdout.strip():
        print(completed.stdout.strip()[-2000:])
    return completed


def main() -> int:
    uv = shutil.which("uv")
    uvx = shutil.which("uvx")
    python = shutil.which("python3")
    if uv is None or uvx is None:
        raise GuardError("uv/uvx not on PATH; run this after astral-sh/setup-uv")
    if python is None:
        raise GuardError("python3 not on PATH; run this after actions/setup-python 3.10")
    reported = run([python, "--version"], dict(os.environ), 30).stdout.strip()
    if not reported.startswith("Python 3.10."):
        raise GuardError(f"the first python3 is {reported!r}, not 3.10; the guard's premise is broken")

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        home = root / "home"
        dist = root / "dist"
        home.mkdir()
        dist.mkdir()
        env = {name: value for name, value in os.environ.items() if name not in ISOLATED_ENV_VARS}
        env["HOME"] = str(home)
        env["UV_PYTHON_INSTALL_DIR"] = str(root / "pythons")
        env.pop("UV_PYTHON", None)  # discovery must follow PATH, not an override

        print("== build the wheel ==")
        built = run([uv, "build", "--out-dir", str(dist)], env, 300)
        if built.returncode != 0:
            raise GuardError(f"uv build failed:\n{built.stderr}")
        wheels = sorted(dist.glob("*.whl"))
        if len(wheels) != 1:
            raise GuardError(f"expected exactly one wheel in {dist}, found {len(wheels)}")
        version, requires_python = wheel_facts(wheels[0])
        spec = f"{DISTRIBUTION}[typesafe]=={version}"
        print(f"== wheel {wheels[0].name}: version {version}, requires-python {requires_python} ==")

        print("== the old class: the plain form must fail on this interpreter ==")
        plain = run([uvx, "--from", spec, DISTRIBUTION, "--help"], env, 240)
        if plain.returncode == 0:
            raise GuardError(
                "the plain form (no --python) started anyway, so the guard proves nothing. "
                f"stdout:\n{plain.stdout}\nstderr:\n{plain.stderr}"
            )

        print("== install through the installer, from the wheel, as a user would ==")
        install_env = dict(env)
        install_env["UV_FIND_LINKS"] = str(dist)  # the pin resolves to the built wheel, not PyPI
        installed = run(
            [uvx, "--from", str(wheels[0]), DISTRIBUTION, "install", "-a", "claude-code", "-y"],
            install_env,
            600,
        )
        if installed.returncode != 0:
            raise GuardError(f"installer failed:\n{installed.stdout}\n{installed.stderr}")

        entry = json.loads((home / ".claude.json").read_text(encoding="utf-8"))["mcpServers"][ENTRY_NAME]
        expected = ["--python", requires_python, "--from", spec, DISTRIBUTION]
        if entry.get("args") != expected:
            raise GuardError(f"written args {entry.get('args')!r} != {expected!r}")
        state = json.loads((home / ".local" / "state" / "jev-mcp" / "install.json").read_text(encoding="utf-8"))
        record = state["targets"]["claude-code"]
        if record.get("verified") is not True:
            raise GuardError(f"the installer did not record a passing verify: {record!r}")
        print(f"ok: the written entry {expected} passed the installer's verify handshake with 3.10 first on PATH")
        return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except GuardError as exc:
        print(f"old-python entry guard failed: {exc}", file=sys.stderr)
        sys.exit(1)
