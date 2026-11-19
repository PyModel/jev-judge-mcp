"""Check that release distributions carry the release tag's version. Stdlib only.

    check_release_version.py dist DIR TAG   DIR holds exactly one wheel and one sdist, both at TAG's version
    check_release_version.py installed TAG  jev_judge_mcp imports and jev-judge-mcp is installed at TAG's version

TAG is the release tag, `v` followed by the version (`v0.1.0`). Exits 1 on a mismatch, 2 on bad usage.
"""

import importlib
import importlib.metadata
import re
import sys
import tarfile
import zipfile
from email.parser import HeaderParser
from pathlib import Path

DISTRIBUTION = "jev-judge-mcp"
PACKAGE = "jev_judge_mcp"


class ReleaseCheckError(Exception):
    pass


def tag_version(tag: str) -> str:
    version = tag.removeprefix("v")
    if version == tag or not re.fullmatch(r"[0-9][0-9A-Za-z.+-]*", version):
        raise ReleaseCheckError(f"release tag {tag!r} is not 'v' followed by a version")
    return version


def _metadata_version(text: str, source: str) -> str:
    metadata = HeaderParser().parsestr(text)
    if metadata.get_all("Name") != [DISTRIBUTION]:
        raise ReleaseCheckError(f"{source} must have one Name field equal to {DISTRIBUTION}")
    versions = metadata.get_all("Version", [])
    if len(versions) != 1 or not versions[0]:
        raise ReleaseCheckError(f"{source} must have one Version field")
    return versions[0]


def _only_member(names: list[str], suffix: str, archive: Path) -> str:
    # Top-level metadata only: `<dist-info>/METADATA` in a wheel, `<name>-<version>/PKG-INFO` in an sdist.
    matches = [name for name in names if name.count("/") == 1 and name.endswith(suffix)]
    if len(matches) != 1:
        raise ReleaseCheckError(f"{archive.name} has {len(matches)} top-level *{suffix} members, expected 1")
    return matches[0]


def wheel_version(wheel: Path) -> str:
    with zipfile.ZipFile(wheel) as archive:
        member = _only_member(archive.namelist(), ".dist-info/METADATA", wheel)
        return _metadata_version(archive.read(member).decode("utf-8"), f"{wheel.name}:{member}")


def sdist_version(sdist: Path) -> str:
    with tarfile.open(sdist, "r:gz") as archive:
        member = _only_member(archive.getnames(), "/PKG-INFO", sdist)
        info = archive.getmember(member)
        extracted = archive.extractfile(info) if info.isfile() else None
        if extracted is None:
            raise ReleaseCheckError(f"{sdist.name}:{member} is not a regular file")
        return _metadata_version(extracted.read().decode("utf-8"), f"{sdist.name}:{member}")


def _only_file(directory: Path, pattern: str) -> Path:
    found = sorted(directory.glob(pattern))
    if len(found) != 1:
        raise ReleaseCheckError(f"{directory} has {len(found)} {pattern} files, expected 1")
    return found[0]


def check_dist(directory: Path, tag: str) -> str:
    expected = tag_version(tag)
    wheel = _only_file(directory, "*.whl")
    sdist = _only_file(directory, "*.tar.gz")
    allowed = {wheel.name, sdist.name, "SHA256SUMS.txt"}
    for asset in directory.iterdir():
        if asset.name not in allowed or asset.is_symlink() or not asset.is_file():
            raise ReleaseCheckError(f"unexpected release asset: {asset.name!r}")
    for archive, actual in ((wheel, wheel_version(wheel)), (sdist, sdist_version(sdist))):
        if actual != expected:
            raise ReleaseCheckError(f"{archive.name} is version {actual}, but tag {tag} needs {expected}")
    if wheel.name != f"{PACKAGE}-{expected}-py3-none-any.whl" or sdist.name != f"{PACKAGE}-{expected}.tar.gz":
        raise ReleaseCheckError(f"release asset names must identify {DISTRIBUTION} at {tag}")
    return expected


def check_installed(tag: str) -> str:
    expected = tag_version(tag)
    importlib.import_module(PACKAGE)
    actual = importlib.metadata.version(DISTRIBUTION)
    if actual != expected:
        raise ReleaseCheckError(f"installed {DISTRIBUTION} is version {actual}, but tag {tag} needs {expected}")
    return expected


def main(argv: list[str]) -> int:
    try:
        match argv:
            case ["dist", directory, tag]:
                version = check_dist(Path(directory), tag)
            case ["installed", tag]:
                version = check_installed(tag)
            case _:
                print(__doc__, file=sys.stderr)
                return 2
    except ReleaseCheckError as error:
        print(f"release version check failed: {error}", file=sys.stderr)
        return 1
    print(f"release version check passed: {version}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
