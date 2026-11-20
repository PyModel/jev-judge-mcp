"""Exercise the published layout and its entry point with an isolated agent home."""

import os
import subprocess
import sys
import sysconfig
from pathlib import Path
from zipfile import ZipFile

from tests.support.stdio import REPO_ROOT


def test_extracted_wheel_install_dry_run(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    subprocess.run(
        ["uv", "build", "--offline", "--wheel", "--out-dir", str(tmp_path)],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
    )
    (wheel,) = tmp_path.glob("*.whl")
    site = tmp_path / "site-packages"
    with ZipFile(wheel) as archive:
        archive.extractall(site)
    # -S excludes the editable checkout's .pth file. Dependencies still come from the test venv.
    paths = [str(site), sysconfig.get_path("purelib"), sysconfig.get_path("platlib")]
    result = subprocess.run(
        [sys.executable, "-S", "-P", "-m", "jev_judge_mcp", "install", "--dry-run", "-a", "claude-code"],
        cwd=home,
        env={"HOME": str(home), "PATH": os.environ["PATH"], "PYTHONPATH": os.pathsep.join(paths), "UV_OFFLINE": "1"},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert f'"jev-judge-mcp=={wheel.name.split("-")[1]}"' in result.stdout
    assert not list(home.iterdir())
