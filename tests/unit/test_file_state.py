"""The file-judgment reader: path scope, typed refusals, the NUL sniff window, and the size cap.

`tests/contract/test_file_judge.py` owns the tool wiring; the filesystem mechanics live here, at
`file_state.py`'s boundary, where `scope` is a parameter whose default (the server's working
directory) is the production behavior.
"""

from collections.abc import Callable
from pathlib import Path

import pytest

from jev_judge_mcp.limits import FILE_JUDGE
from jev_judge_mcp.tools import file_state
from jev_judge_mcp.tools.base import ToolError


def make(path: Path, data: bytes | str) -> Path:
    if isinstance(data, str):
        path.write_text(data, encoding="utf-8")
    else:
        path.write_bytes(data)
    return path


def refuses(code: str) -> Callable[[Callable[[], object]], None]:
    def matcher(call: Callable[[], object]) -> None:
        with pytest.raises(ToolError) as raised:
            call()
        assert raised.value.code == code, raised.value

    return matcher


def test_a_relative_path_resolves_inside_the_scope(tmp_path: Path) -> None:
    make(tmp_path / "notes.txt", "hello")
    resolved = file_state.resolve_scoped("notes.txt", scope=tmp_path)
    assert resolved == (tmp_path / "notes.txt").resolve()
    assert file_state.read_state(resolved) == "hello"


def test_a_dotdot_escape_is_refused(tmp_path: Path) -> None:
    outside = make(tmp_path.parent / "outside.txt", "secret")
    refuses("path_outside_scope")(lambda: file_state.resolve_scoped(f"../{outside.name}", scope=tmp_path))


def test_a_symlink_to_outside_is_refused_and_a_symlink_to_inside_is_allowed(tmp_path: Path) -> None:
    inside_dir = tmp_path / "in"
    inside_dir.mkdir()
    inside = make(inside_dir / "real.txt", "kept")
    outside = make(tmp_path.parent / f"escape-{tmp_path.name}.txt", "secret")
    (tmp_path / "link-in").symlink_to(inside)
    (tmp_path / "link-out").symlink_to(outside)
    assert file_state.resolve_scoped("link-in", scope=tmp_path) == inside.resolve()
    refuses("path_outside_scope")(lambda: file_state.resolve_scoped("link-out", scope=tmp_path))


def test_the_default_scope_is_the_working_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    make(tmp_path / "here.txt", "yes")
    assert file_state.resolve_scoped("here.txt") == (tmp_path / "here.txt").resolve()
    refuses("path_outside_scope")(lambda: file_state.resolve_scoped(str(tmp_path.parent / "elsewhere.txt")))


def test_a_missing_file_is_not_found(tmp_path: Path) -> None:
    refuses("not_found")(lambda: file_state.read_state(tmp_path / "missing.txt"))


def test_a_directory_is_not_a_file(tmp_path: Path) -> None:
    (tmp_path / "dir").mkdir()
    refuses("not_a_file")(lambda: file_state.read_state(tmp_path / "dir"))


def test_a_secret_store_refuses_before_any_read(tmp_path: Path) -> None:
    make(tmp_path / ".env", "TOKEN=deadbeef")
    refuses("secret_file")(lambda: file_state.read_state(tmp_path / ".env"))


def test_the_secret_store_family_and_the_stand_ins(tmp_path: Path) -> None:
    refused = (
        ".env",
        ".env.production",
        ".env.local",
        "server.pem",
        "ca.key",
        "cert.p12",
        "sig.pfx",
        "id_rsa",
        "id_ed25519",
        "id_ecdsa",
        ".npmrc",
        ".pypirc",
        ".netrc",
    )
    for name in refused:
        make(tmp_path / name, "material")
        assert file_state.is_secret_store(tmp_path / name), name
    allowed = (".env.example", ".env.sample", ".env.template", "notes.txt", "keys.md")
    for name in allowed:
        make(tmp_path / name, "fine to read")
        assert not file_state.is_secret_store(tmp_path / name), name
        assert file_state.read_state(tmp_path / name) == "fine to read"


def test_a_credential_literal_is_redacted_before_it_becomes_state(tmp_path: Path) -> None:
    literal = "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ1234567890"
    content = file_state.read_state(make(tmp_path / "notes.txt", f"token: {literal}\nkeep: this line"))
    assert literal not in content
    assert "[redacted]" in content
    assert "keep: this line" in content  # text without a literal passes through unchanged


def test_a_nul_inside_the_window_is_binary(tmp_path: Path) -> None:
    data = b"a" * (FILE_JUDGE.binary_sniff_bytes - 1) + b"\x00"
    refuses("binary_file")(lambda: file_state.read_state(make(tmp_path / "bin.bin", data)))


def test_a_nul_just_outside_the_window_is_text(tmp_path: Path) -> None:
    data = b"a" * FILE_JUDGE.binary_sniff_bytes + b"\x00"
    assert "\x00" in file_state.read_state(make(tmp_path / "late.bin", data))


def test_a_file_at_the_unit_cap_is_read_and_one_unit_over_is_refused(tmp_path: Path) -> None:
    at_cap = file_state.read_state(make(tmp_path / "at.txt", "a" * FILE_JUDGE.file_units_max))
    assert len(at_cap) == FILE_JUDGE.file_units_max
    over = make(tmp_path / "over.txt", "a" * (FILE_JUDGE.file_units_max + 1))
    refuses("file_too_large")(lambda: file_state.read_state(over))


def test_a_byte_giant_is_refused(tmp_path: Path) -> None:
    # Four UTF-8 bytes measure at least one UTF-16 unit, so any file over 4 x file_units_max bytes
    # is over the unit cap before it is decoded. Astral padding gives the giant the smallest unit
    # count per byte a valid encoding can reach, and it is still over.
    astral = "😀" * (100_001)
    giant = make(tmp_path / "giant.txt", astral)
    assert giant.stat().st_size > 4 * FILE_JUDGE.file_units_max
    refuses("file_too_large")(lambda: file_state.read_state(giant))


def test_undecodable_bytes_decode_with_replacement_instead_of_crashing(tmp_path: Path) -> None:
    content = file_state.read_state(make(tmp_path / "broken.txt", b"ok \xff\xfe bad"))
    assert "\ufffd" in content
