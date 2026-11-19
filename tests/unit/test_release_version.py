"""scripts/check_release_version.py: the Release workflow's version gate, run as the workflow runs it."""

import hashlib
import importlib.metadata
import io
import json
import os
import re
import subprocess
import sys
import tarfile
import textwrap
import tomllib
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


@pytest.mark.parametrize("change", ["none", "changed", "missing", "duplicate", "version"])
def test_scout_006_publish_checks_verified_bytes(tmp_path: Path, change: str) -> None:
    assets = tmp_path / "assets"
    assets.mkdir()
    dist(assets)
    receipt = tmp_path / "receipt"
    receipt.mkdir()
    manifest = receipt / "SHA256SUMS.txt"
    files = sorted(assets.iterdir(), key=lambda path: not path.name.endswith(".whl"))
    manifest.write_text("".join(f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}\n" for p in files))
    if change == "changed":
        with files[0].open("ab") as stream:
            stream.write(b"changed")
    elif change == "missing":
        files[1].unlink()
    elif change == "duplicate":
        write_wheel(assets, "0.2.0")
    # An independently sourced checksum has to win even over a replacement release checksum.
    (assets / "SHA256SUMS.txt").write_text("untrusted release checksum")
    marker = tmp_path / "published"
    uv = tmp_path / "uv"
    uv.write_text('#!/bin/bash\n[[ "$1" == publish ]] || exit 2\ntouch "$RUNNER_TEMP/published"\n')
    uv.chmod(0o700)
    command = (
        workflow_command("Validate PyPI distributions")
        + '\ncd "$RUNNER_TEMP/assets"\n'
        + workflow_command("Upload to PyPI through trusted publishing")
    )
    result = subprocess.run(
        ["bash", "-ec", command],
        cwd=tmp_path,
        env={
            **os.environ,
            "PATH": f"{tmp_path}:{os.environ['PATH']}",
            "RUNNER_TEMP": str(tmp_path),
            "GITHUB_WORKSPACE": str(SCRIPT.parents[1]),
            "GITHUB_OUTPUT": str(tmp_path / "output"),
            "VERIFIED_MANIFEST": hashlib.sha256(manifest.read_bytes()).hexdigest(),
            "TAG": "v0.2.0" if change == "version" else "v0.1.0",
        },
        capture_output=True,
        text=True,
        check=False,
    )
    assert (result.returncode == 0) == (change == "none"), result.stderr
    assert marker.exists() == (change == "none")


@pytest.mark.parametrize("extra", ["notes.txt", ".hidden", "directory", "symlink"])
def test_dist_rejects_unlisted_or_nonregular_assets(tmp_path: Path, extra: str) -> None:
    assets = tmp_path / "assets"
    assets.mkdir()
    dist(assets)
    path = assets / extra
    if extra == "directory":
        path.mkdir()
    elif extra == "symlink":
        path = next(assets.glob("*.whl"))
        wheel = path.read_bytes()
        path.unlink()
        target = tmp_path / "target.whl"
        target.write_bytes(wheel)
        path.symlink_to(target)
    else:
        path.write_text("release notes")
    result = run("dist", str(assets), "v0.1.0")
    assert result.returncode == 1, result.stdout
    assert "asset" in result.stderr


def test_release_guide_describes_manual_publish_authority() -> None:
    workflow = (SCRIPT.parents[1] / ".github/workflows/release.yml").read_text()
    guide = (SCRIPT.parents[1] / "CONTRIBUTING.md").read_text()
    assert "github.event_name == 'workflow_dispatch' && inputs.republish_tag != ''" in workflow
    assert "even when `PYPI_PUBLISH` is unset or false" in guide
    assert all(word in guide for word in ("`verified_run`", "unexpired", "`main`", "`pypi`"))


def test_release_guide_matches_current_version_configuration() -> None:
    project = SCRIPT.parents[1]
    release_config = json.loads((project / "release-please-config.json").read_text())
    if "release-as" not in release_config:
        assert "pins it with `release-as`" not in (project / "CONTRIBUTING.md").read_text()


def test_package_code_runs_without_release_write_permissions() -> None:
    workflow = (SCRIPT.parents[1] / ".github/workflows/release.yml").read_text()
    jobs = re.split(r"^  [\w-]+:\n", workflow.split("\njobs:\n")[1], flags=re.M)[1:]
    executions = [job for job in jobs if "uv build" in job or "uv pip install" in job]
    assert executions
    for job in executions:
        assert "contents: read" in job
        assert ": write" not in job
        assert "persist-credentials: true" not in job


def test_release_build_requires_hashed_backend_and_dependencies() -> None:
    project = SCRIPT.parents[1]
    build_system = tomllib.loads((project / "pyproject.toml").read_text())["build-system"]
    assert re.fullmatch(r"hatchling==[\d.]+", build_system["requires"][0])
    workflow = (project / ".github/workflows/release.yml").read_text()
    assert "--require-hashes" in workflow
    assert "uv build --no-build-isolation" in workflow
    assert re.search(rf"{re.escape(build_system['requires'][0])} +--hash=sha256:[0-9a-f]{{64}}", workflow)


def test_dist_requires_the_project_distribution(tmp_path: Path) -> None:
    dist(tmp_path)
    wheel = next(tmp_path.glob("*.whl"))
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("other-0.1.0.dist-info/METADATA", metadata("0.1.0").replace("jev-judge-mcp", "other"))
    result = run("dist", str(tmp_path), "v0.1.0")
    assert result.returncode == 1
    assert "Name" in result.stderr


def workflow_command(name: str) -> str:
    workflow = (SCRIPT.parents[1] / ".github/workflows/release.yml").read_text()
    step = workflow.split(f"- name: {name}\n", 1)[1]
    match = re.search(r"^        run: \|\n((?:          [^\n]*\n|\n)+)", step, re.M)
    assert match
    return textwrap.dedent(match[1])


@pytest.mark.parametrize(
    "change",
    ["none", "extra", "replaced", "missing", "pending", "public-extra", "public-replaced", "public-unreadable"],
)
def test_github_publication_checks_all_draft_assets(tmp_path: Path, change: str) -> None:
    assets = tmp_path / "assets"
    assets.mkdir()
    dist(assets)
    files = sorted(assets.iterdir(), key=lambda path: not path.name.endswith(".whl"))
    checksum = assets / "SHA256SUMS.txt"
    checksum.write_text("".join(f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}\n" for p in files))
    inventory = [
        {"name": p.name, "digest": f"sha256:{hashlib.sha256(p.read_bytes()).hexdigest()}", "state": "uploaded"}
        for p in [*files, checksum]
    ]
    if change == "extra":
        inventory.append({**inventory[0], "name": "notes.txt"})
    elif change == "replaced":
        inventory[0]["digest"] = "sha256:" + "0" * 64
    elif change == "missing":
        inventory.pop()
    elif change == "pending":
        inventory[0]["state"] = "starter"
    (tmp_path / "inventory.json").write_text(json.dumps(inventory))
    public_inventory = inventory.copy()
    if change == "public-extra":
        public_inventory.append({**inventory[0], "name": "notes.txt"})
    elif change == "public-replaced":
        public_inventory[0] = {**inventory[0], "digest": "sha256:" + "1" * 64}
    if change != "public-unreadable":
        (tmp_path / "public-inventory.json").write_text(json.dumps(public_inventory))
    gh = tmp_path / "gh"
    gh.write_text(
        "#!/bin/bash\n"
        'case "$*" in\n'
        '  *"release view"*"--jq .isDraft") echo true ;;\n'
        '  *"release view"*) echo \'{"databaseId":123,"isDraft":true,"tagName":"v0.1.0"}\' ;;\n'
        '  *"/assets?"*) if [[ -e "$RUNNER_TEMP/published" ]]; then cat "$RUNNER_TEMP/public-inventory.json"; '
        'else cat "$RUNNER_TEMP/inventory.json"; fi ;;\n'
        '  *"release edit"*|*"PATCH"*) touch "$RUNNER_TEMP/published" ;;\n'
        "  *) exit 2 ;;\nesac\n"
    )
    gh.chmod(0o700)
    result = subprocess.run(
        ["bash", "-ec", workflow_command("Publish the release")],
        cwd=tmp_path,
        env={
            "PATH": f"{tmp_path}:{os.environ['PATH']}",
            "RUNNER_TEMP": str(tmp_path),
            "TAG": "v0.1.0",
            "GH_REPO": "fixture/repo",
            "BUILT_MANIFEST": hashlib.sha256(checksum.read_bytes()).hexdigest(),
        },
        capture_output=True,
        text=True,
        check=False,
    )
    assert (result.returncode == 0) == (change == "none"), result.stderr
    assert (tmp_path / "published").exists() == (change == "none" or change.startswith("public-"))
    if change.startswith("public-"):
        assert "Published release v0.1.0" in result.stderr and "verified receipt" in result.stderr


def test_release_please_pin_matches_reviewed_trust_decision() -> None:
    project = SCRIPT.parents[1]
    workflow = (project / ".github/workflows/release.yml").read_text()
    adr = (project / "docs/adr/0060-release-pr-ci-is-maintainer-approved.md").read_text()
    version_job = re.split(r"^  [\w-]+:\n", workflow.split("\n  version:\n", 1)[1], maxsplit=1, flags=re.M)[0]
    permissions = version_job.split("    permissions:\n", 1)[1].split("\n    outputs:", 1)[0]
    assert set(re.findall(r"^      ([\w-]+): (\w+)", permissions, flags=re.M)) == {
        ("contents", "write"),
        ("pull-requests", "write"),
        ("issues", "write"),
    }
    assert len(re.findall(r"^      - ", version_job, flags=re.M)) == 1
    pin = re.search(r"uses: (googleapis/release-please-action@[0-9a-f]{40})\b", version_job)
    assert pin and pin[1] in adr
    assert "token: ${{ github.token }}" in version_job and "secrets" not in version_job
    assert "any pin change requires maintainer review" in " ".join(adr.lower().split())
    assert "`pypi-retry` jobs keep" not in adr


def test_receipt_saved_on_a_fresh_runner_after_install() -> None:
    workflow = (SCRIPT.parents[1] / ".github/workflows/release.yml").read_text()
    jobs = re.split(r"^  [\w-]+:\n", workflow.split("\njobs:\n")[1], flags=re.M)[1:]
    [receipt_job] = [job for job in jobs if "name: verified-" in job]
    assert "uv pip install " not in receipt_job
    assert "needs: [version, build, verify]" in receipt_job
    assert "needs.build.outputs.manifest_digest" in receipt_job


def test_draft_upload_preserves_existing_files_on_retry() -> None:
    upload = workflow_command("Upload staged distributions")
    assert "--clobber" not in upload
    assert "gh release upload" in upload


def test_release_metadata_must_be_a_regular_sdist_member(tmp_path: Path) -> None:
    dist(tmp_path)
    path = next(tmp_path.glob("*.tar.gz"))
    data = metadata("0.1.0").encode()
    with tarfile.open(path, "w:gz") as archive:
        target = tarfile.TarInfo("jev_judge_mcp-0.1.0/metadata")
        target.size = len(data)
        archive.addfile(target, io.BytesIO(data))
        link = tarfile.TarInfo("jev_judge_mcp-0.1.0/PKG-INFO")
        link.type = tarfile.SYMTYPE
        link.linkname = "metadata"
        archive.addfile(link)
    result = run("dist", str(tmp_path), "v0.1.0")
    assert result.returncode == 1
    assert "not a regular file" in result.stderr
