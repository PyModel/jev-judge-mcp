"""Published sources and fixtures must not identify a contributor's machine."""

import hashlib
import os
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
# An account formerly present in recorded examples. Use its digest so the guard itself is portable and clean.
RECORDED_ACCOUNT_SHA256 = "a7cdf5d0586b392473dd0cd08c9ba833240006a8a7310bf9bc8bf1aefdfaeadb"
HOME_PATH = re.compile(rb"/(?:Users|home)/\.?[A-Za-z0-9_\x80-\xff][A-Za-z0-9._\x80-\xff-]*")
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
DARWIN_TEMP = re.compile(rb"(?:/var/folders/|-var-folders-)([a-z0-9_]+)", re.I)
ACCOUNT_CANDIDATE = re.compile(rb"(?=(p[a-z]{4}))", re.I)


def contains_host_identifier(data: bytes) -> bool:
    if set(DARWIN_TEMP.findall(data)) - {b"probe", b"none", b"xyz"} or set(HOME_PATH.findall(data)) - EXAMPLE_HOMES:
        return True
    return any(
        hashlib.sha256(word.lower()).hexdigest() == RECORDED_ACCOUNT_SHA256
        for word in set(ACCOUNT_CANDIDATE.findall(data))
    )


def test_tracked_files_omit_host_identifiers() -> None:
    listing = subprocess.check_output(["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"], cwd=ROOT)
    names = [os.fsdecode(raw) for raw in listing.split(b"\0") if raw]
    assert names, "no tracked files to inspect"
    offenders: list[str] = []
    for name in names:
        path = ROOT / name
        if not path.exists() and not path.is_symlink():
            continue
        data = os.fsencode(os.readlink(path)) if path.is_symlink() else path.read_bytes()
        if contains_host_identifier(data):
            offenders.append(name)
    assert not offenders, f"tracked files contain host identifiers: {offenders}"


def test_portable_home_and_temp_controls() -> None:
    for prefix in (b"/Users/", b"/home/"):
        for account in (b"_contributor", b".local-auditor", "équipe".encode()):
            assert contains_host_identifier(prefix + account + b"/work")
    for root in (b"/private/var/" + b"folders/", b"-private-var-" + b"folders-"):
        assert contains_host_identifier(root + b"q1/sample/T/work")
    assert not contains_host_identifier(b"/home/example/work")
