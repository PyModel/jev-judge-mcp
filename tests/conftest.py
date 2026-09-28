"""The suite's hermeticity guard: no test may write into a fixed shared location.

The leak of record (found at d6f73c6): `test_the_shim_readiness_wait_is_valid_python_and_fatal`
passed `Path("/tmp")` where its `tmp_path` belonged, so every run wrote world-readable
`/tmp/typesafe.key` and `/tmp/provider.key` and nothing ever removed them. The failure class is
"a test writes into a fixed shared location outside pytest's tmp_path or a mkdtemp root it
removes". Two checks below fail the exact test that does so, both attributed to this process
only — a before/after diff of the shared directories cannot work here, because unrelated
processes (an agent supervisor, another lane's pre-push gate) legitimately create entries in
those same roots while the suite runs.

Write-time check: every write the test process makes through the pathlib surface, `open()`, or
the directory-creating `os` functions must land outside the fixed shared roots (/tmp, /var/tmp,
and `tempfile.gettempdir()`) or inside a root this test owns — its tmp_path (under pytest's
basetemp) or a directory `tempfile.mkdtemp` returned during the test. The leak of record dies
at the offending `write_text` call, naming the test and the path.

Teardown check: a mkdtemp root this test created must be gone once every fixture has torn down;
a root that survives is residue the test failed to remove, and the test errors naming it.

Scope, deliberately narrow: the checks see only this process's calls, so writes made by spawned
subprocesses (agents, brokers, uv, node) are invisible here — their cleanup is asserted by the
tests that spawn them (the confinement suite's leftover checks). fd-level writes and `io.open`
direct calls are likewise out of scope; nothing in this suite writes that way. The blocked roots
are the fixed shared TEMP directories only — every fixed-path write this suite has had lived
there (/tmp for the key leak, $TMPDIR for the reaper test's forged orphan): on this machine and
on GitHub runners the checkout itself lives under $HOME, so blocking home would flag every lazy
`__pycache__` write under the venv — a fixed `~/.foo` leak gets a blocked root the day it
appears.

Authoring gate (.agents/skills/test-audit/SKILL.md): the protected behavior is suite
hermeticity; the credible regression is any helper handed a fixed shared directory — proven by
reverting the d6f73c6 fix, which turns this guard's write-time check into the reported error;
no production seam is added — this is plain pytest configuration.
"""

import builtins
import os
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any, cast

import pytest

_BLOCKED_ROOTS = frozenset(
    {
        root.resolve()
        for root in (
            Path("/tmp"),
            Path("/var/tmp"),
            Path(tempfile.gettempdir()),
        )
        if root.is_dir()
    }
)


def _resolved(target: str | os.PathLike[str]) -> Path:
    return Path(target).resolve()


def _shared(path: Path) -> bool:
    return any(path == root or path.is_relative_to(root) for root in _BLOCKED_ROOTS)


@pytest.fixture(autouse=True)
def _writes_stay_in_test_owned_roots(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> Iterator[None]:
    """Refuse in-process writes into fixed shared roots; keep mkdtemp roots owned and removed."""
    owned: set[Path] = {tmp_path_factory.getbasetemp().resolve()}
    mkdtemp_roots: set[Path] = set()
    inside_mkdtemp = False
    node = cast("pytest.Item", request.node).nodeid  # pyright: ignore[reportUnknownMemberType]

    real_mkdtemp = tempfile.mkdtemp

    def _tracked_mkdtemp(*args: Any, **kwargs: Any) -> str:
        nonlocal inside_mkdtemp
        inside_mkdtemp = True
        try:
            made = cast("str", real_mkdtemp(*args, **kwargs))
        finally:
            inside_mkdtemp = False
        root = Path(made).resolve()
        owned.add(root)
        mkdtemp_roots.add(root)
        return made

    def _refuse(target: str | os.PathLike[str], via: str) -> None:
        path = _resolved(target)
        if _shared(path) and not any(path == root or path.is_relative_to(root) for root in owned):
            raise AssertionError(
                f"{node}: {via} targets {path}, a fixed shared location; "
                "a test may write only under its tmp_path or a mkdtemp root it removes"
            )

    real_write_text = Path.write_text
    real_write_bytes = Path.write_bytes
    real_open = Path.open
    real_mkdir = Path.mkdir
    real_touch = Path.touch
    real_symlink_to = Path.symlink_to
    real_builtin_open = builtins.open
    real_os_mkdir = os.mkdir
    real_os_makedirs = os.makedirs
    real_os_rename = os.rename
    real_os_replace = os.replace
    real_os_symlink = os.symlink
    real_os_link = os.link

    def _write_text(self: Path, data: str, *args: Any, **kwargs: Any) -> int:
        _refuse(self, "Path.write_text")
        return real_write_text(self, data, *args, **kwargs)

    def _write_bytes(self: Path, data: bytes, *args: Any, **kwargs: Any) -> int:
        _refuse(self, "Path.write_bytes")
        return real_write_bytes(self, data, *args, **kwargs)

    def _open(self: Path, mode: str = "r", *args: Any, **kwargs: Any) -> Any:
        if any(flag in mode for flag in "wax+"):
            _refuse(self, "Path.open")
        return real_open(self, mode, *args, **kwargs)

    def _mkdir(self: Path, *args: Any, **kwargs: Any) -> None:
        if not inside_mkdtemp:
            _refuse(self, "Path.mkdir")
        real_mkdir(self, *args, **kwargs)

    def _touch(self: Path, *args: Any, **kwargs: Any) -> None:
        _refuse(self, "Path.touch")
        real_touch(self, *args, **kwargs)

    def _symlink_to(self: Path, target: str | os.PathLike[str], *args: Any, **kwargs: Any) -> None:
        _refuse(self, "Path.symlink_to")
        real_symlink_to(self, target, *args, **kwargs)

    def _builtin_open(file: str | os.PathLike[str] | int, mode: str = "r", *args: Any, **kwargs: Any) -> Any:
        if not isinstance(file, int) and any(flag in mode for flag in "wax+"):
            _refuse(file, "open")
        return real_builtin_open(file, mode, *args, **kwargs)

    def _os_mkdir(path: str | os.PathLike[str], *args: Any, **kwargs: Any) -> None:
        if not inside_mkdtemp:
            _refuse(path, "os.mkdir")
        real_os_mkdir(path, *args, **kwargs)

    def _os_makedirs(path: str | os.PathLike[str], *args: Any, **kwargs: Any) -> None:
        _refuse(path, "os.makedirs")
        real_os_makedirs(path, *args, **kwargs)

    def _os_rename(src: str | os.PathLike[str], dst: str | os.PathLike[str], *args: Any, **kwargs: Any) -> None:
        _refuse(dst, "os.rename")
        real_os_rename(src, dst, *args, **kwargs)

    def _os_replace(src: str | os.PathLike[str], dst: str | os.PathLike[str], *args: Any, **kwargs: Any) -> None:
        _refuse(dst, "os.replace")
        real_os_replace(src, dst, *args, **kwargs)

    def _os_symlink(src: str | os.PathLike[str], dst: str | os.PathLike[str], *args: Any, **kwargs: Any) -> None:
        _refuse(dst, "os.symlink")
        real_os_symlink(src, dst, *args, **kwargs)

    def _os_link(src: str | os.PathLike[str], dst: str | os.PathLike[str], *args: Any, **kwargs: Any) -> None:
        _refuse(dst, "os.link")
        real_os_link(src, dst, *args, **kwargs)

    monkeypatch.setattr(tempfile, "mkdtemp", _tracked_mkdtemp)
    monkeypatch.setattr(Path, "write_text", _write_text)
    monkeypatch.setattr(Path, "write_bytes", _write_bytes)
    monkeypatch.setattr(Path, "open", _open)
    monkeypatch.setattr(Path, "mkdir", _mkdir)
    monkeypatch.setattr(Path, "touch", _touch)
    monkeypatch.setattr(Path, "symlink_to", _symlink_to)
    monkeypatch.setattr(builtins, "open", _builtin_open)
    monkeypatch.setattr(os, "mkdir", _os_mkdir)
    monkeypatch.setattr(os, "makedirs", _os_makedirs)
    monkeypatch.setattr(os, "rename", _os_rename)
    monkeypatch.setattr(os, "replace", _os_replace)
    monkeypatch.setattr(os, "symlink", _os_symlink)
    monkeypatch.setattr(os, "link", _os_link)
    yield
    leftover = sorted(str(root) for root in mkdtemp_roots if root.exists())
    if leftover:
        raise AssertionError(
            f"{node} never removed mkdtemp root(s) {', '.join(leftover)}: "
            "a test may write only under its tmp_path or a mkdtemp root it removes"
        )
