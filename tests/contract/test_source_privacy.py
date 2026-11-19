"""Published sources and fixtures must not identify a contributor's machine."""

import hashlib
import os
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
# An account formerly present in recorded examples. Use its digest so the guard itself is portable and clean.
RECORDED_ACCOUNT_SHA256 = "a7cdf5d0586b392473dd0cd08c9ba833240006a8a7310bf9bc8bf1aefdfaeadb"
HOME_PATH = re.compile(rb"/(?:Users|home)/[A-Za-z0-9][A-Za-z0-9._-]*")
EXAMPLE_HOMES = {
    b"/Users/dev",
    b"/Users/example",
    b"/Users/operator",
    b"/Users/x",
    b"/home/example",
    b"/home/me",
    b"/home/other",
    b"/home/runner",
    b"/home/user",
}
# Darwin's per-user temporary directory can also appear flattened in Claude's project key.
DARWIN_TEMP = re.compile(rb"(?:/var/folders/|-var-folders-)[a-z0-9]{2}[/\-][a-z0-9_]{20,}", re.I)
ACCOUNT_CANDIDATE = re.compile(rb"(?=(p[a-z]{4}))", re.I)


def contains_host_identifier(data: bytes) -> bool:
    if DARWIN_TEMP.search(data) or set(HOME_PATH.findall(data)) - EXAMPLE_HOMES:
        return True
    return any(
        hashlib.sha256(word.lower()).hexdigest() == RECORDED_ACCOUNT_SHA256
        for word in set(ACCOUNT_CANDIDATE.findall(data))
    )


def test_tracked_files_omit_host_identifiers() -> None:
    listing = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT)
    names = [os.fsdecode(raw) for raw in listing.split(b"\0") if raw]
    assert names, "no tracked files to inspect"
    offenders: list[str] = []
    for name in names:
        path = ROOT / name
        data = os.fsencode(os.readlink(path)) if path.is_symlink() else path.read_bytes()
        if contains_host_identifier(data):
            offenders.append(name)
    assert not offenders, f"tracked files contain host identifiers: {offenders}"
