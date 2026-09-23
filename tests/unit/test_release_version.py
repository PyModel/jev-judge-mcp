"""scripts/check_release_version.py: the Release workflow's version gate, run as the workflow runs it."""

import importlib.metadata
import io
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check_release_version.py"


def run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True, check=False)


def metadata(version: str) -> str:
    return f"Metadata-Version: 2.4\nName: jev-judge-mcp\nVersion: {version}\n"


def write_wheel(directory: Path, version: str) -> None:
    with zipfile.ZipFile(directory / f"jev_judge_mcp-{version}-py3-none-any.whl", "w") as wheel:
        wheel.writestr("jev_judge_mcp/__init__.py", "")
        wheel.writestr(f"jev_judge_mcp-{version}.dist-info/METADATA", metadata(version))


def write_sdist(directory: Path, version: str) -> None:
    content = metadata(version).encode()
    info = tarfile.TarInfo(f"jev_judge_mcp-{version}/PKG-INFO")
    info.size = len(content)
    with tarfile.open(directory / f"jev_judge_mcp-{version}.tar.gz", "w:gz") as sdist:
        sdist.addfile(info, io.BytesIO(content))


def dist(tmp_path: Path, wheel: str = "0.1.0", sdist: str = "0.1.0") -> Path:
    write_wheel(tmp_path, wheel)
    write_sdist(tmp_path, sdist)
    return tmp_path


def test_dist_at_the_tag_version_passes(tmp_path: Path) -> None:
    result = run("dist", str(dist(tmp_path)), "v0.1.0")
    assert result.returncode == 0, result.stderr
    assert result.stdout == "release version check passed: 0.1.0\n"


@pytest.mark.parametrize(
    ("wheel", "sdist", "reason"),
    [
        ("0.1.1", "0.1.0", "jev_judge_mcp-0.1.1-py3-none-any.whl is version 0.1.1, but tag v0.1.0 needs 0.1.0"),
        ("0.1.0", "0.0.9", "jev_judge_mcp-0.0.9.tar.gz is version 0.0.9, but tag v0.1.0 needs 0.1.0"),
    ],
)
def test_dist_off_the_tag_version_fails(tmp_path: Path, wheel: str, sdist: str, reason: str) -> None:
    result = run("dist", str(dist(tmp_path, wheel, sdist)), "v0.1.0")
    assert result.returncode == 1
    assert result.stderr == f"release version check failed: {reason}\n"


def test_dist_needs_exactly_one_wheel(tmp_path: Path) -> None:
    write_wheel(dist(tmp_path), "0.1.1")
    result = run("dist", str(tmp_path), "v0.1.0")
    assert result.returncode == 1
    assert "has 2 *.whl files, expected 1" in result.stderr


def test_dist_needs_an_sdist(tmp_path: Path) -> None:
    write_wheel(tmp_path, "0.1.0")
    result = run("dist", str(tmp_path), "v0.1.0")
    assert result.returncode == 1
    assert "has 0 *.tar.gz files, expected 1" in result.stderr


@pytest.mark.parametrize("tag", ["0.1.0", "v"])
def test_tag_must_be_v_then_a_version(tmp_path: Path, tag: str) -> None:
    result = run("dist", str(dist(tmp_path)), tag)
    assert result.returncode == 1
    assert f"release tag {tag!r} is not 'v' followed by a version" in result.stderr


def test_installed_version_matching_the_tag_passes() -> None:
    installed = importlib.metadata.version("jev-judge-mcp")
    result = run("installed", f"v{installed}")
    assert result.returncode == 0, result.stderr


def test_installed_version_off_the_tag_fails() -> None:
    installed = importlib.metadata.version("jev-judge-mcp")
    result = run("installed", "v999.0.0")
    assert result.returncode == 1
    assert f"installed jev-judge-mcp is version {installed}, but tag v999.0.0 needs 999.0.0" in result.stderr


def test_bad_usage_exits_2() -> None:
    assert run("dist", "only-a-directory").returncode == 2
