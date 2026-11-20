"""Installer fixtures use a throwaway home. They never read or write the real user config."""

import json
import os
import signal
import stat
import subprocess
import sys
import tomllib
from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import replace
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from shutil import which

import pytest
from pydantic import SecretStr

from jev_judge_mcp.domain import is_json_object
from jev_judge_mcp.install import cli as install_cli
from jev_judge_mcp.install import engine as install_engine
from jev_judge_mcp.install import fs as install_fs
from jev_judge_mcp.install import jsonedit
from jev_judge_mcp.install import launch as launch_module
from jev_judge_mcp.install import redact as install_redact
from jev_judge_mcp.install import verify as verify_module
from jev_judge_mcp.install.engine import Request, run
from jev_judge_mcp.install.errors import InstallError
from jev_judge_mcp.install.fs import atomic_write, entry_hash, write_backup, write_state
from jev_judge_mcp.install.launch import (
    PACKAGE,
    Launch,
    checkout_launch,
    claude_code_entry,
    claude_desktop_entry,
    find_package_root,
    local_install_warning,
    pi_direct_tools,
    pi_entry,
    pypi_launch,
    requires_python,
    supported_spec,
)
from jev_judge_mcp.install.layout import Layout
from jev_judge_mcp.install.redact import redact_text
from jev_judge_mcp.install.verify import EXPECTED_TOOLS, VerifyError, verify_command
from jev_judge_mcp.server import installer_requested
from jev_judge_mcp.settings import load_settings
from jev_judge_mcp.tools import TOOLS
from tests.support.secrets import secret_env

UVX = "/opt/uv/uvx"
SPEC = "/work/jev-mcp"
PIN = f"{PACKAGE}=={version(PACKAGE)}"
LAUNCH = Launch(uvx=UVX, spec=SPEC)
PYPI_LAUNCH = pypi_launch(UVX)
MARKER = "synthetic-secret-value"
TARGETS = ("claude-code", "claude-desktop", "codex", "cursor", "opencode", "pi", "omp", "pythinker")


def layout(home: Path) -> Layout:
    return Layout(
        home=home,
        claude_config_dir=None,
        codex_home=None,
        xdg_config_home=home / ".config",
        pi_agent_dir=None,
        omp_profile=None,
        pythinker_home=None,
        xdg_state_home=home / ".local" / "state",
        applications=home / "Applications",
        application_support=home / "Library" / "Application Support",
    )


def execute(
    home: Path,
    *,
    agents: tuple[str, ...] = (),
    desktop_key: str | None = None,
    secrets: tuple[str, ...] = (),
    dry_run: bool = False,
    remove: bool = False,
    force: bool = False,
    before_write: Callable[[Path], None] | None = None,
    launch: Launch = LAUNCH,
    verify: Callable[[list[str]], None] | None = None,
) -> tuple[int, str]:
    result = run(
        Request(
            layout=layout(home),
            launch=launch,
            agents=agents,
            assume_yes=True,
            platform="darwin",
            bins={},
            desktop_key=desktop_key,
            secrets=secrets,
            dry_run=dry_run,
            remove=remove,
            force=force,
            before_write=before_write,
            verify=verify,
        )
    )
    if MARKER in result.text:
        raise AssertionError("installer printed a secret")
    return result.code, result.text


def _run_raw(request: Request) -> tuple[int, str]:
    result = run(request)
    return result.code, result.text


def assert_secret_absent(path: Path) -> None:
    if path.is_file() and MARKER in path.read_text(encoding="utf-8"):
        raise AssertionError("a state file contains a secret")


def test_no_arguments_stay_on_the_stdio_server() -> None:
    assert installer_requested(["jev-judge-mcp"]) is False
    assert installer_requested(["jev-judge-mcp", "install"]) is True
    assert installer_requested(["jev-judge-mcp", "--help"]) is False


def test_launch_uses_the_local_path_and_the_distribution_name() -> None:
    launch = checkout_launch(UVX)
    args = launch.args()
    assert args[-1] == PACKAGE
    assert args[0] == "--python"  # the installed metadata declares Requires-Python (ADR-0053)
    assert Path(launch.spec).is_absolute()
    assert not any(part == "jev-mcp" or part.startswith("jev-mcp==") or part.startswith("jev-mcp[") for part in args)


def test_default_launch_pins_the_installed_version() -> None:
    """From a checkout or a wheel, the default spec is the running package pinned on PyPI (ADR-0051)."""
    launch = pypi_launch(UVX)
    assert launch.spec == PIN
    assert launch.args() == ["--python", requires_python() or "", "--from", PIN, PACKAGE]


def test_default_launch_without_metadata_names_the_alternative(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing(name: str) -> str:
        raise PackageNotFoundError(name)

    monkeypatch.setattr(launch_module, "version", missing)
    with pytest.raises(InstallError, match="install the package in this environment"):
        pypi_launch(UVX)


# --- ADR-0053: every entry requests a Python the package itself declares. ---


def test_pypi_launch_requests_the_metadata_python(monkeypatch: pytest.MonkeyPatch) -> None:
    """The request is derived from the installed metadata, not hardcoded to one version."""
    _installed_like_wheel(monkeypatch, requires_python=">=3.12,<4")
    launch = pypi_launch(UVX)
    assert launch.python == ">=3.12,<4"
    assert launch.args() == ["--python", ">=3.12,<4", "--from", PIN, PACKAGE]


def test_checkout_launch_requests_the_metadata_python(monkeypatch: pytest.MonkeyPatch) -> None:
    _installed_like_wheel(monkeypatch, requires_python=">=3.12,<4")
    launch = checkout_launch(UVX, package_root=Path("/work/jev-mcp"))
    assert launch.args() == ["--python", ">=3.12,<4", "--from", SPEC, PACKAGE]


def test_a_wrapped_specifier_is_collapsed_for_uvx(monkeypatch: pytest.MonkeyPatch) -> None:
    """Metadata headers can line-wrap; uvx still gets one clean specifier argument."""
    _installed_like_wheel(monkeypatch, requires_python=">=3.12,\n <4")
    assert requires_python() == ">=3.12, <4"


def test_without_a_metadata_python_the_entry_stays_an_argument_array_without_the_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _installed_like_wheel(monkeypatch, requires_python="")
    assert pypi_launch(UVX).args() == ["--from", PIN, PACKAGE]


def test_requires_python_without_an_install_is_none(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing(name: str) -> _FakeDistribution:
        raise PackageNotFoundError(name)

    monkeypatch.setattr(launch_module, "distribution", missing)
    assert requires_python() is None
    assert local_install_warning() is None


@pytest.fixture
def wheel_layout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Model a wheel below an unrelated project, regardless of pytest's outer directory."""
    origin = tmp_path / "site-packages" / "jev_judge_mcp" / "install" / "launch.py"
    origin.parent.mkdir(parents=True)
    origin.touch()
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "other-project"\n', encoding="utf-8")
    monkeypatch.setattr(launch_module, "__file__", str(origin))
    outer_manifests = {parent / "pyproject.toml" for parent in tmp_path.resolve().parents}
    real_stat = Path.stat

    def visible_stat(path: Path, *, follow_symlinks: bool = True) -> os.stat_result:
        if path in outer_manifests:
            raise FileNotFoundError(path)
        return real_stat(path, follow_symlinks=follow_symlinks)

    monkeypatch.setattr(Path, "stat", visible_stat)


@pytest.mark.usefixtures("wheel_layout")
def test_find_package_root_without_a_checkout_names_the_flag() -> None:
    with pytest.raises(InstallError, match=r"source checkout.*--from-checkout"):
        find_package_root()


def test_specs_accept_exactly_the_two_supported_shapes() -> None:
    assert supported_spec(PIN)
    assert supported_spec("jev-judge-mcp==0.1.1rc1")
    assert supported_spec("jev-judge-mcp==1!2.3rc1.post2.dev3+local.4")
    assert supported_spec(SPEC)
    assert supported_spec("/home/me/jev-judge-mcp")
    for wrong in (
        PACKAGE,
        "jev-mcp",
        f"{PACKAGE}[typesafe]==0.1.1",
        f"{PACKAGE}==",
        f"{PACKAGE}==0.1.1 more",
        f"{PACKAGE}==0.1.*",
        f"{PACKAGE}==1,!=2",
        f"{PACKAGE}==latest",
        "work/jev-mcp",
        "",
    ):
        assert not supported_spec(wrong), wrong


def test_engine_refuses_an_unsupported_spec(tmp_path: Path) -> None:
    with pytest.raises(InstallError, match="unsupported launch spec"):
        execute(tmp_path, agents=("claude-code",), launch=Launch(uvx=UVX, spec="jev-mcp"))


# --- ADR-0051 launch identity, through the command line. Every test uses a throwaway HOME. ---


def _fake_home(monkeypatch: pytest.MonkeyPatch, home: Path) -> None:
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    for name in (
        "XDG_CONFIG_HOME",
        "XDG_STATE_HOME",
        "CLAUDE_CONFIG_DIR",
        "CODEX_HOME",
        "PI_CODING_AGENT_DIR",
        "PYTHINKER_CODE_HOME",
        "OMP_PROFILE",
        "PI_PROFILE",
    ):
        monkeypatch.delenv(name, raising=False)
    _installed_like_wheel(monkeypatch)

    def uvx(name: str) -> str | None:
        return UVX

    monkeypatch.setattr(install_cli, "which", uvx)


class _FakeDistribution:
    """Stands in for the installed distribution: metadata and direct_url.json, nothing else."""

    def __init__(self, *, metadata: dict[str, str], direct_url: str | None) -> None:
        self.metadata: dict[str, str] = metadata
        self._direct_url = direct_url

    def read_text(self, name: str) -> str | None:
        return self._direct_url if name == "direct_url.json" else None


def _installed_like_wheel(monkeypatch: pytest.MonkeyPatch, requires_python: str = ">=3.12") -> None:
    """A PyPI wheel install: Requires-Python present, no direct_url.json (ADR-0053)."""
    dist = _FakeDistribution(metadata={"Requires-Python": requires_python}, direct_url=None)

    def found(_name: str) -> _FakeDistribution:
        return dist

    monkeypatch.setattr(launch_module, "distribution", found)


def _installed_like_local_checkout(monkeypatch: pytest.MonkeyPatch, requires_python: str = ">=3.12") -> None:
    """An editable source install: importlib.metadata records a file:// direct_url (ADR-0053)."""
    direct_url = json.dumps({"url": "file:///work/jev-mcp/src/jev_judge_mcp", "dir_info": {"editable": True}})
    dist = _FakeDistribution(metadata={"Requires-Python": requires_python}, direct_url=direct_url)

    def found(_name: str) -> _FakeDistribution:
        return dist

    monkeypatch.setattr(launch_module, "distribution", found)


@pytest.mark.usefixtures("wheel_layout")
def test_wheel_layout_dry_run_renders_the_pinned_pypi_spec(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The critique's wheel probe, inverted: no checkout anywhere, and `install` still works."""

    _fake_home(monkeypatch, tmp_path)
    code = install_cli.main(["--dry-run", "-a", "claude-code"])
    out = capsys.readouterr().out
    assert code == 0, out
    assert PIN in out
    assert SPEC not in out
    assert list(tmp_path.rglob("*.json")) == []  # dry-run wrote nothing


def _uvx_reporting(directory: Path, line: str) -> Path:
    """A uvx whose `--version` is `line`. The real `which` finds it; nothing else is stubbed."""
    directory.mkdir(parents=True, exist_ok=True)
    script = directory / "uvx"
    script.write_text(f"#!/bin/sh\nprintf '%s\\n' {line!r}\n", encoding="utf-8")
    script.chmod(0o755)
    return script


def _qualifying_uvx(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Point `which` at a uvx new enough for `--from-checkout`. The path stub never ran before."""
    script = _uvx_reporting(tmp_path / "bin", "uvx 0.12.19 (bea138450 2026-09-24 aarch64-apple-darwin)")

    def found(name: str) -> str | None:
        return str(script) if name == "uvx" else None

    monkeypatch.setattr(install_cli, "which", found)


def test_checkout_flag_renders_the_checkout_spec(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _fake_home(monkeypatch, tmp_path)
    _qualifying_uvx(monkeypatch, tmp_path)
    checkout = str(find_package_root())
    code = install_cli.main(["--dry-run", "-a", "claude-code", "--from-checkout"])
    assert code == 0
    out = capsys.readouterr().out
    assert f"{checkout}" in out
    assert f"=={version(PACKAGE)}" not in out


def test_default_from_a_checkout_is_still_the_pypi_spec(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _fake_home(monkeypatch, tmp_path)
    assert find_package_root() == Path(__file__).resolve().parents[2]
    code = install_cli.main(["--dry-run", "-a", "claude-code"])
    assert code == 0
    out = capsys.readouterr().out
    added = [line[1:] for line in out.splitlines() if line.startswith("+") and not line.startswith("+++ ")]
    entry = json.loads("\n".join(added))["mcpServers"]["jev"]
    assert entry["args"] == ["--python", ">=3.12", "--from", PIN, PACKAGE]


# --- ADR-0053: an unreleased tree says so before pinning the published build. ---


def test_install_from_a_local_checkout_warns_about_the_pypi_pin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _fake_home(monkeypatch, tmp_path)
    _installed_like_local_checkout(monkeypatch)
    code = install_cli.main(["--dry-run", "-a", "claude-code"])
    out = capsys.readouterr().out
    assert code == 0, out
    assert "local checkout" in out
    assert "does not include your local changes" in out
    assert f"{PACKAGE}=={version(PACKAGE)}" in out  # names the pinned version
    assert "--from-checkout" in out
    assert PIN in out  # the plan still shows the default pin


def test_install_from_a_pypi_wheel_prints_no_local_checkout_note(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _fake_home(monkeypatch, tmp_path)  # already a wheel-like install: no direct_url.json
    code = install_cli.main(["--dry-run", "-a", "claude-code"])
    out = capsys.readouterr().out
    assert code == 0, out
    assert "local checkout" not in out


def test_from_checkout_prints_no_local_checkout_note(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _fake_home(monkeypatch, tmp_path)
    _installed_like_local_checkout(monkeypatch)
    _qualifying_uvx(monkeypatch, tmp_path)
    code = install_cli.main(["--dry-run", "-a", "claude-code", "--from-checkout"])
    out = capsys.readouterr().out
    assert code == 0, out
    assert "local checkout" not in out
    assert f"{find_package_root()}" in out


def test_from_checkout_requires_uvx_that_honors_cache_keys(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """uvx 0.9.24 keeps the first checkout build; 0.10.10 is the first version that does not."""
    _fake_home(monkeypatch, tmp_path)
    monkeypatch.setattr(install_cli, "which", which)

    old = _uvx_reporting(tmp_path / "old", "uvx 0.9.24 (0fda1525e 2026-01-09)")
    monkeypatch.setenv("PATH", f"{old.parent}{os.pathsep}{os.environ.get('PATH', '')}")
    code = install_cli.main(["--from-checkout", "-y", "--dry-run", "-a", "claude-code"])
    out = capsys.readouterr().out
    assert code == 1, out
    assert "0.9.24" in out
    assert "0.10.10" in out

    new = _uvx_reporting(tmp_path / "new", "uvx 0.10.10 (x)")
    monkeypatch.setenv("PATH", f"{new.parent}{os.pathsep}{os.environ.get('PATH', '')}")
    code = install_cli.main(["--from-checkout", "-y", "--dry-run", "-a", "claude-code"])
    out = capsys.readouterr().out
    assert code == 0, out
    assert f"{find_package_root()}" in out


@pytest.mark.usefixtures("wheel_layout")
def test_checkout_flag_without_a_checkout_fails_accurately(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _fake_home(monkeypatch, tmp_path)
    _qualifying_uvx(monkeypatch, tmp_path)
    code = install_cli.main(["--dry-run", "-a", "claude-code", "--from-checkout"])
    assert code == 1
    out = capsys.readouterr().out
    assert "source checkout" in out
    assert "before publication" not in out


def test_checkout_flag_explains_an_unreadable_ancestor_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _fake_home(monkeypatch, tmp_path)
    _qualifying_uvx(monkeypatch, tmp_path)
    origin = tmp_path / "site-packages" / "launch.py"
    origin.parent.mkdir()
    origin.touch()
    monkeypatch.setattr(launch_module, "__file__", str(origin))
    manifest = tmp_path / "pyproject.toml"
    real_stat = Path.stat

    def denied(path: Path, *, follow_symlinks: bool = True) -> os.stat_result:
        if path == manifest:
            raise PermissionError("permission denied")
        return real_stat(path, follow_symlinks=follow_symlinks)

    monkeypatch.setattr(Path, "stat", denied)
    assert install_cli.main(["--dry-run", "-a", "claude-code", "--from-checkout"]) == 1
    message = capsys.readouterr().out
    assert str(manifest) in message
    assert "permission" in message.lower()
    assert "--from-checkout" in message


@pytest.mark.parametrize("package", ["npm:pi-mcp-adapter", "git:example.test/owner/pi-mcp-adapter"])
def test_each_target_gains_one_entry(tmp_path: Path, package: str) -> None:
    _pi_adapter(tmp_path, package)
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".codex").mkdir()
    (tmp_path / ".config" / "opencode").mkdir(parents=True)
    (tmp_path / ".omp").mkdir()
    (tmp_path / ".pythinker-code").mkdir()
    desktop = tmp_path / "Library" / "Application Support" / "Claude"
    desktop.mkdir(parents=True)
    code, text = execute(
        tmp_path,
        agents=TARGETS,
        desktop_key=MARKER,
        secrets=(MARKER,),
    )
    assert code == 0, text
    claude = json.loads((tmp_path / ".claude.json").read_text(encoding="utf-8"))
    assert claude["mcpServers"]["jev"]["args"] == ["--from", SPEC, PACKAGE]
    assert claude["mcpServers"]["jev"]["env"]["TYPESAFE_API_KEY"] == "${TYPESAFE_API_KEY}"
    desk = json.loads((desktop / "claude_desktop_config.json").read_text(encoding="utf-8"))
    assert desk["mcpServers"]["jev"]["env"]["TYPESAFE_API_KEY"] == MARKER
    codex = (tmp_path / ".codex" / "config.toml").read_text(encoding="utf-8")
    assert 'env_vars = ["TYPESAFE_API_KEY"]' in codex
    assert MARKER not in codex
    opencode = json.loads((tmp_path / ".config" / "opencode" / "opencode.json").read_text(encoding="utf-8"))
    assert opencode["mcp"]["jev"]["environment"]["TYPESAFE_API_KEY"] == "{env:TYPESAFE_API_KEY}"
    pi = json.loads((tmp_path / ".pi" / "agent" / "mcp.json").read_text(encoding="utf-8"))
    assert pi["mcpServers"]["jev"]["env"]["TYPESAFE_API_KEY"] == "${TYPESAFE_API_KEY}"
    assert pi["mcpServers"]["jev"]["lifecycle"] == "eager"
    assert pi["mcpServers"]["jev"]["directTools"] == pi_direct_tools()
    assert pi["mcpServers"]["jev"]["toolPrefix"] == "none"
    omp = json.loads((tmp_path / ".omp" / "agent" / "mcp.json").read_text(encoding="utf-8"))
    assert "$schema" not in omp
    assert omp["mcpServers"]["jev"]["command"] == UVX
    pythinker = json.loads((tmp_path / ".pythinker-code" / "mcp.json").read_text(encoding="utf-8"))
    assert "env" not in pythinker["mcpServers"]["jev"]
    state = tmp_path / ".local" / "state" / "jev-mcp" / "install.json"
    assert_secret_absent(state)
    assert stat.S_IMODE(state.stat().st_mode) == 0o600
    assert stat.S_IMODE(state.parent.stat().st_mode) == 0o700
    backups = list((state.parent / "backups").rglob("*"))
    # The targets were created fresh, so there is no prior file to back up.
    assert not any(path.is_file() for path in backups)


def test_pi_entry_puts_the_jev_tools_in_the_initial_list() -> None:
    """The Pi entry must not hide the tools behind the adapter's lazy proxy.

    A lazy, proxy-only entry leaves the model with nothing jev-shaped until it runs the gateway
    dance itself, and recorded bench runs show it never does (ADR-0036). The adapter registers
    direct tools from an eager connection in the session's first tool list, under the published
    names when `toolPrefix` is `"none"` (pi-mcp-adapter 2.37.0, `direct-tool-surface.ts`).
    """
    entry = pi_entry(LAUNCH)
    assert entry["lifecycle"] == "eager"
    assert entry["directTools"] == pi_direct_tools()
    assert entry["toolPrefix"] == "none"
    # With no prefix the registered names are exactly the published tool names.
    assert all(tool.name.startswith("jev_") for tool in TOOLS)


def test_second_run_does_not_rewrite(tmp_path: Path) -> None:
    code, _ = execute(tmp_path, agents=("claude-code",))
    assert code == 0
    path = tmp_path / ".claude.json"
    stamp = path.stat().st_mtime_ns
    mode = stat.S_IMODE(path.stat().st_mode)
    assert mode == 0o600
    code, text = execute(tmp_path, agents=("claude-code",))
    assert code == 0
    assert "up to date" in text
    assert path.stat().st_mtime_ns == stamp


def test_dry_run_writes_nothing_and_prints_no_secret(tmp_path: Path) -> None:
    support = tmp_path / "Library" / "Application Support" / "Claude"
    support.mkdir(parents=True)
    config = support / "claude_desktop_config.json"
    config.write_text('{"preferences": {"theme": "dark"}, "mcpServers": {}}\n', encoding="utf-8")
    os.chmod(config, 0o600)
    before = _fingerprint(tmp_path)
    code, text = execute(
        tmp_path,
        agents=("claude-desktop",),
        desktop_key=MARKER,
        secrets=(MARKER,),
        dry_run=True,
    )
    assert code == 0
    assert "dry-run: wrote nothing" in text
    assert "[redacted]" in text
    assert _fingerprint(tmp_path) == before
    if MARKER in config.read_text(encoding="utf-8"):
        raise AssertionError("dry-run wrote a secret")


@pytest.mark.parametrize(
    ("template", "siblings"),
    [
        ('{ "mcpServers": { "jev": ENTRY }, "keep": 3 }', {}),
        ('{\n  "mcpServers": {"jev": ENTRY},\n  "keep": 3\n}', {}),
        ('{\n  "mcpServers": {"jev": ENTRY, "other": {}}, "keep": 3\n}', {"other": {}}),
        ('{\n  "mcpServers": {\n    "a": {}, "jev": ENTRY, "b": {}\n  }, "keep": 3\n}', {"a": {}, "b": {}}),
        ('{"mcpServers": {\n    "jev": ENTRY\n    , "other": {}\n  }, "keep": 3}', {"other": {}}),
        (
            '{\n  "mcpServers": {\n    "jev": ENTRY,\n'
            '    "evaluate": {"command": "/usr/bin/evaluate", "args": []}\n  }, "keep": 3\n}',
            {"evaluate": {"command": "/usr/bin/evaluate", "args": []}},
        ),
        ('{\n  "mcpServers": {\n    "other": {},\n    "jev": ENTRY\n  }, "keep": 3\n}', {"other": {}}),
    ],
    ids=["single-line", "inline-only", "inline-first", "inline-middle", "comma-first", "multiline-first", "last"],
)
def test_remove_drops_only_our_entry(tmp_path: Path, template: str, siblings: dict[str, object]) -> None:
    assert execute(tmp_path, agents=("claude-code",))[0] == 0
    path = layout(tmp_path).claude_code_file()
    entry = json.loads(path.read_text())["mcpServers"]["jev"]
    path.write_text(template.replace("ENTRY", json.dumps(entry)))
    code, text = execute(tmp_path, agents=("claude-code",), remove=True)
    assert code == 0, text
    assert json.loads(path.read_text()) == {"mcpServers": siblings, "keep": 3}


def test_remove_leaves_a_foreign_entry(tmp_path: Path) -> None:
    path = tmp_path / ".claude.json"
    original = json.dumps({"mcpServers": {"jev": {"command": "/bin/other", "args": []}}}, indent=2) + "\n"
    path.write_text(original, encoding="utf-8")
    code, text = execute(tmp_path, agents=("claude-code",), remove=True)
    assert code == 0, text
    assert "foreign" in text
    assert path.read_text(encoding="utf-8") == original


@pytest.mark.parametrize("agents", [(), ("pi",)], ids=["autodetected", "explicit"])
def test_remove_pi_after_its_adapter_is_uninstalled(tmp_path: Path, agents: tuple[str, ...]) -> None:
    _pi_adapter(tmp_path, "npm:pi-mcp-adapter")
    pi = layout(tmp_path)
    other = {"command": "/usr/bin/other"}
    pi.pi_file().write_text(json.dumps({"mcpServers": {"other": other}}), encoding="utf-8")
    assert execute(tmp_path, agents=("pi",))[0] == 0
    pi.pi_settings().write_text('{"packages": []}', encoding="utf-8")

    code, message = execute(tmp_path, agents=agents, remove=True)
    assert code == 0, message
    assert json.loads(pi.pi_file().read_text(encoding="utf-8"))["mcpServers"] == {"other": other}
    assert _state_targets(tmp_path) == {}


@pytest.mark.parametrize("package", ["npm:pi-mcp-adapter", "git:example.test/owner/pi-mcp-adapter"])
def test_unknown_shapes_are_left_untouched(tmp_path: Path, package: str) -> None:
    samples = {
        "claude-code": (tmp_path / ".claude.json", '{"mcpServers": ["nope"]}\n'),
        "claude-desktop": (
            tmp_path / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json",
            '{"mcpServers": "nope"}\n',
        ),
        "codex": (tmp_path / ".codex" / "config.toml", 'mcp_servers = "nope"\n'),
        "opencode": (tmp_path / ".config" / "opencode" / "opencode.json", '{"mcp": {"servers": ["nope"]}}\n'),
        "pi": (tmp_path / ".pi" / "agent" / "mcp.json", '{"mcpServers": 1}\n'),
        "omp": (tmp_path / ".omp" / "agent" / "mcp.json", '["not-an-object"]\n'),
        "pythinker": (tmp_path / ".pythinker-code" / "mcp.json", '{"mcpServers": null}\n'),
    }
    _pi_adapter(tmp_path, package)
    for target, (path, body) in samples.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
        before = path.read_bytes()
        code, text = execute(tmp_path, agents=(target,), desktop_key=MARKER, secrets=(MARKER,))
        assert path.read_bytes() == before
        assert code == 1
        assert "left unchanged" in text


def test_malformed_json_is_left_untouched(tmp_path: Path) -> None:
    path = tmp_path / ".claude.json"
    path.write_text("{", encoding="utf-8")
    before = path.read_bytes()
    code, text = execute(tmp_path, agents=("claude-code",))
    assert code == 1
    assert path.read_bytes() == before
    assert "left unchanged" in text


def test_change_during_install_writes_nothing(tmp_path: Path) -> None:
    path = tmp_path / ".claude.json"

    def disturb(target: Path) -> None:
        target.write_text("{}\n", encoding="utf-8")

    code, text = execute(tmp_path, agents=("claude-code",), before_write=disturb)
    assert code == 1
    assert "changed during install, re-run" in text
    assert "jev" not in path.read_text(encoding="utf-8")


def test_desktop_golden_redacts_the_key(tmp_path: Path) -> None:
    support = tmp_path / "Library" / "Application Support" / "Claude"
    support.mkdir(parents=True)
    code, text = execute(tmp_path, agents=("claude-desktop",), desktop_key=MARKER, secrets=(MARKER,))
    assert code == 0, text
    actual = (support / "claude_desktop_config.json").read_text(encoding="utf-8").replace(MARKER, "[redacted]")
    assert actual == _golden("claude-desktop.json")


@pytest.mark.parametrize("package", ["npm:pi-mcp-adapter", "git:example.test/owner/pi-mcp-adapter"])
def test_json_targets_match_goldens(tmp_path: Path, package: str) -> None:
    _pi_adapter(tmp_path, package)
    code, text = execute(tmp_path, agents=("claude-code", "pi", "omp", "pythinker"))
    assert code == 0, text
    assert (tmp_path / ".claude.json").read_text(encoding="utf-8") == _golden("claude-code.json")
    assert (tmp_path / ".pi" / "agent" / "mcp.json").read_text(encoding="utf-8") == _golden("pi.json")
    assert (tmp_path / ".omp" / "agent" / "mcp.json").read_text(encoding="utf-8") == _golden("omp.json")
    assert (tmp_path / ".pythinker-code" / "mcp.json").read_text(encoding="utf-8") == _golden("pythinker.json")


def test_cursor_target_gains_the_entry_and_detection(tmp_path: Path) -> None:
    (tmp_path / ".cursor").mkdir()
    code, text = execute(tmp_path)
    assert code == 0, text
    assert "detected cursor" in text
    assert (tmp_path / ".cursor" / "mcp.json").read_text(encoding="utf-8") == _golden("cursor.json")


def test_cursor_detected_from_the_binary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _fake_home(monkeypatch, tmp_path)

    def on_path(name: str) -> str | None:
        return UVX if name in {"uvx", "cursor"} else None

    monkeypatch.setattr(install_cli, "which", on_path)
    assert install_cli.main(["--all", "--dry-run"]) == 0
    report = capsys.readouterr().out
    assert "detected cursor: cursor is on PATH" in report
    assert str(layout(tmp_path).cursor_file()) in report


def test_a_bom_in_a_json_config_keeps_every_other_server(tmp_path: Path) -> None:
    """A UTF-8 BOM must not turn a config into garbage: parse it, edit it, keep the rest."""
    path = tmp_path / ".cursor" / "mcp.json"
    path.parent.mkdir(parents=True)
    path.write_bytes('\ufeff{"mcpServers": {"other": {"command": "x"}}}'.encode())
    code, text = execute(tmp_path, agents=("cursor",))
    assert code == 0, text
    document = json.loads(path.read_text(encoding="utf-8-sig"))
    assert document["mcpServers"]["other"] == {"command": "x"}
    assert document["mcpServers"]["jev"]["command"] == UVX


def test_a_bom_in_the_codex_config_is_tolerated(tmp_path: Path) -> None:
    path = tmp_path / ".codex" / "config.toml"
    path.parent.mkdir(parents=True)
    path.write_bytes('\ufeffmodel = "gpt-5"\n'.encode())
    code, text = execute(tmp_path, agents=("codex",))
    assert code == 0, text
    assert "[mcp_servers.jev]" in path.read_text(encoding="utf-8")


def test_foreign_entry_is_skipped_until_forced(tmp_path: Path) -> None:
    path = tmp_path / ".claude.json"
    original = json.dumps({"mcpServers": {"jev": {"command": "/bin/other", "args": []}}}, indent=2) + "\n"
    path.write_text(original, encoding="utf-8")
    code, text = execute(tmp_path, agents=("claude-code",))
    assert code == 0, text
    assert "foreign entry" in text
    assert path.read_text(encoding="utf-8") == original
    code, text = execute(tmp_path, agents=("claude-code",), force=True)
    assert code == 0, text
    assert "overwrites: jev" in text
    written = json.loads(path.read_text(encoding="utf-8"))
    assert written["mcpServers"]["jev"]["command"] == UVX


@pytest.mark.parametrize(
    ("original", "parents", "existing", "comment"),
    [
        (
            '{"mcp": {"servers": {"other": {"type": "local", "command": ["echo"]}}}}',
            ["mcp", "servers"],
            {"other": {"type": "local", "command": ["echo"]}},
            "",
        ),
        (
            '{"mcp": {"servers": {"type": "local", "command": ["echo"]}}}',
            ["mcp"],
            {"servers": {"type": "local", "command": ["echo"]}},
            "",
        ),
        ('{"mcp": {"servers": {"enabled": false}}}', ["mcp"], {"servers": {"enabled": False}}, ""),
        ('{"mcp": {"other": {"command": ["echo",],},},}', ["mcp"], {"other": {"command": ["echo"]}}, ""),
        ('{"mcp": {/* add servers here */}}', ["mcp"], {}, "/* add servers here */"),
        ('{\r\n  "mcp": {\r\n    "other": {} // keep me\r\n  }\r\n}', ["mcp"], {"other": {}}, "// keep me"),
    ],
    ids=["v2", "v1-server-named-servers", "v1-disabled-server", "trailing-commas", "inline-comment", "crlf"],
)
def test_opencode_server_map(
    tmp_path: Path, original: str, parents: list[str], existing: dict[str, object], comment: str
) -> None:
    path = layout(tmp_path).opencode_dir() / "opencode.jsonc"
    path.parent.mkdir(parents=True)
    path.write_bytes(original.encode())
    code, text = execute(tmp_path, agents=("opencode",))
    assert code == 0, text
    updated = path.read_bytes().decode()
    if "\r\n" in original:
        assert "\n" not in updated.replace("\r\n", "")
    if comment:
        assert comment in updated and updated.index(comment) < updated.index('"jev"')
    document = jsonedit.loads(updated)
    for key in parents:
        assert is_json_object(document)
        document = document[key]
    assert is_json_object(document)
    entry = document.pop("jev")
    assert is_json_object(entry) and entry["type"] == "local"
    assert document == existing


@pytest.mark.parametrize(
    "original",
    [
        '{"mcp": {}, "mcp": {}}',
        '{"mcp": {"servers": {}, "servers": {}}}',
        '{"mcp": {"jev": {}, "j\\u0065v": {}}}',
    ],
    ids=["root-path", "nested-path", "entry"],
)
def test_opencode_refuses_duplicate_edited_keys(tmp_path: Path, original: str) -> None:
    path = layout(tmp_path).opencode_dir() / "opencode.jsonc"
    path.parent.mkdir(parents=True)
    path.write_text(original)
    code, text = execute(tmp_path, agents=("opencode",), force=True)
    assert code == 1 and "duplicate key" in text
    assert path.read_text() == original


@pytest.mark.parametrize(
    ("operation", "context"),
    [
        ("add", "sibling"),
        ("update", "sibling"),
        ("remove", "sibling"),
        ("add", "empty-parent"),
        ("remove", "own-heading"),
    ],
)
def test_codex_preserves_the_next_table_heading(tmp_path: Path, operation: str, context: str) -> None:
    path = layout(tmp_path).codex_file()
    path.parent.mkdir()
    prefix = "[mcp_servers]\n" if context == "empty-parent" else '[mcp_servers.other]\ncommand = "echo"\n'
    if operation != "add":
        prefix += "\n"
        if context == "own-heading":
            prefix += "# Heading for Jev\n"
        prefix += '[mcp_servers.jev]\ncommand = "old-jev"\n'
        _record_entry(tmp_path, "codex", path, {"command": "old-jev"})
    heading = "# Heading for the following table\n[features]\nuse_feature = true\n"
    path.write_text(prefix + "\n" + heading)
    code, text = execute(tmp_path, agents=("codex",), remove=operation == "remove")
    assert code == 0, text
    updated = path.read_text()
    assert heading in updated
    if context == "own-heading":
        assert "# Heading for Jev" in updated
    assert ("jev" in tomllib.loads(updated)["mcp_servers"]) is (operation != "remove")


@pytest.mark.parametrize("corruption", ["invalid", "wrong-entry", "lost-sibling", "lost-heading", "lost-comment"])
def test_installer_refuses_a_corrupt_render_before_writing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, corruption: str
) -> None:
    target = "codex" if corruption == "lost-heading" else "claude-code"
    paths = layout(tmp_path)
    path = paths.codex_file() if target == "codex" else paths.claude_code_file()
    original = '{"mcpServers":{"other":{"command":"echo"}},"keep":true}'
    if corruption == "lost-heading":
        original = '[mcp_servers.other]\ncommand = "echo"\n\n# keep\n[features]\none = true\n'
    elif corruption == "lost-comment":
        original = "/* keep */" + original
    path.parent.mkdir(exist_ok=True)
    path.write_text(original)
    render = install_engine.render_json
    render_codex = install_engine.render_codex

    def corrupt(target: str, text: str, name: str, entry: Mapping[str, object]) -> str:
        rendered = render(target, text, name, entry)
        if target != "claude-code":
            return rendered
        if corruption == "lost-comment":
            return rendered.replace("/* keep */", "")
        result = json.loads(rendered)
        if corruption == "invalid":
            return "{"
        if corruption == "wrong-entry":
            result["mcpServers"][name] = {}
        else:
            del result["mcpServers"]["other"]
        return json.dumps(result)

    def omit_heading(text: str, name: str, entry: Mapping[str, object]) -> str:
        return render_codex(text, name, entry).replace("# keep\n", "")

    monkeypatch.setattr(install_engine, "render_json", corrupt)
    monkeypatch.setattr(install_engine, "render_codex", omit_heading)
    code, text = execute(tmp_path, agents=(target, "cursor"))
    assert code == 1 and "rendered config" in text and "left unchanged" in text
    assert path.read_text() == original
    assert layout(tmp_path).cursor_file().exists()


def test_codex_and_opencode_keep_comments(tmp_path: Path) -> None:
    codex = tmp_path / ".codex" / "config.toml"
    codex.parent.mkdir(parents=True)
    codex.write_text('# keep-me comment\nmodel = "gpt"\n\n[mcp_servers.other]\ncommand = "echo"\n', encoding="utf-8")
    opencode = tmp_path / ".config" / "opencode" / "opencode.jsonc"
    opencode.parent.mkdir(parents=True)
    opencode.write_text(
        '{\n  // keep-me comment\n  "mcp": {\n    "other": {"type": "local", "command": ["echo"]}\n  }\n}\n',
        encoding="utf-8",
    )
    code, text = execute(tmp_path, agents=("codex", "opencode"))
    assert code == 0, text
    codex_text = codex.read_text(encoding="utf-8")
    opencode_text = opencode.read_text(encoding="utf-8")
    assert "# keep-me comment" in codex_text
    assert "[mcp_servers.other]" in codex_text
    assert "env_vars" in codex_text
    assert "// keep-me comment" in opencode_text
    assert '"other"' in opencode_text
    assert "{env:TYPESAFE_API_KEY}" in opencode_text
    assert codex_text == _golden("codex.toml")
    assert opencode_text == _golden("opencode.jsonc")


def test_literal_write_is_private_and_reference_write_keeps_mode(tmp_path: Path) -> None:
    support = tmp_path / "Library" / "Application Support" / "Claude"
    support.mkdir(parents=True)
    desktop = support / "claude_desktop_config.json"
    desktop.write_text('{"preferences": {}}\n', encoding="utf-8")
    os.chmod(desktop, 0o644)
    code, text = execute(tmp_path, agents=("claude-desktop",), desktop_key=MARKER, secrets=(MARKER,))
    assert code == 0, text
    assert stat.S_IMODE(desktop.stat().st_mode) == 0o600

    claude = tmp_path / ".claude.json"
    claude.write_text("{}\n", encoding="utf-8")
    os.chmod(claude, 0o644)
    code, text = execute(tmp_path, agents=("claude-code",))
    assert code == 0, text
    assert stat.S_IMODE(claude.stat().st_mode) == 0o644
    stored = json.loads(claude.read_text(encoding="utf-8"))
    assert stored["mcpServers"]["jev"]["env"]["TYPESAFE_API_KEY"] == "${TYPESAFE_API_KEY}"


def test_loose_mode_is_reported_on_rerun_without_printing_the_key(tmp_path: Path) -> None:
    support = tmp_path / "Library" / "Application Support" / "Claude"
    support.mkdir(parents=True)
    code, _ = execute(tmp_path, agents=("claude-desktop",), desktop_key=MARKER, secrets=(MARKER,))
    assert code == 0
    config = support / "claude_desktop_config.json"
    assert stat.S_IMODE(config.stat().st_mode) == 0o600
    os.chmod(config, 0o644)
    code, text = execute(tmp_path, agents=("claude-desktop",), desktop_key=MARKER, secrets=(MARKER,))
    assert code == 0
    assert "looser than 0600" in text
    assert "GUI apps rewrite this file" in text


def test_backup_is_private_and_state_has_no_key(tmp_path: Path) -> None:
    path = tmp_path / ".claude.json"
    path.write_text(
        json.dumps({"mcpServers": {"evaluate": {"env": {"TYPESAFE_API_KEY": MARKER}}}}) + "\n",
        encoding="utf-8",
    )
    os.chmod(path, 0o600)
    code, _ = execute(tmp_path, agents=("claude-code",))
    assert code == 0
    state_dir = tmp_path / ".local" / "state" / "jev-mcp"
    state = state_dir / "install.json"
    assert_secret_absent(state)
    assert stat.S_IMODE(state.stat().st_mode) == 0o600
    assert stat.S_IMODE(state_dir.stat().st_mode) == 0o700
    copies = [item for item in (state_dir / "backups").rglob("*") if item.is_file()]
    assert len(copies) == 1
    assert stat.S_IMODE(copies[0].stat().st_mode) == 0o600
    assert stat.S_IMODE(copies[0].parent.stat().st_mode) == 0o700


def test_pi_without_adapter_stops_with_instructions(tmp_path: Path) -> None:
    (tmp_path / ".pi" / "agent").mkdir(parents=True)
    (tmp_path / ".claude").mkdir()
    code, text = execute(tmp_path, agents=("claude-code", "pi"))
    assert code == 1
    assert "pi install npm:pi-mcp-adapter" in text
    assert not (tmp_path / ".pi" / "agent" / "mcp.json").exists()
    assert (tmp_path / ".claude.json").is_file()


def test_pythinker_desktop_id_does_not_match_another_app(tmp_path: Path) -> None:
    _plist(tmp_path, "PyThinker.app", "org.pymodel.pythinker")
    (tmp_path / ".pythinker-code").mkdir()
    code, text = execute(tmp_path, agents=("pythinker",), desktop_key=MARKER, secrets=(MARKER,))
    assert code == 0, text
    entry = json.loads((tmp_path / ".pythinker-code" / "mcp.json").read_text(encoding="utf-8"))
    assert "env" not in entry["mcpServers"]["jev"]
    assert MARKER not in (tmp_path / ".pythinker-code" / "mcp.json").read_text(encoding="utf-8")


def test_pythinker_desktop_stores_the_key_only_with_consent(tmp_path: Path) -> None:
    _plist(tmp_path, "Pythinker.app", "com.pythinker.desktop")
    (tmp_path / ".pythinker-code").mkdir()
    code, text = execute(tmp_path, agents=("pythinker",))
    assert code == 0, text
    assert "not given the key" in text
    bare = json.loads((tmp_path / ".pythinker-code" / "mcp.json").read_text(encoding="utf-8"))
    assert "env" not in bare["mcpServers"]["jev"]
    code, _ = execute(tmp_path, agents=("pythinker",), desktop_key=MARKER, secrets=(MARKER,), force=True)
    assert code == 0
    stored = json.loads((tmp_path / ".pythinker-code" / "mcp.json").read_text(encoding="utf-8"))
    assert stored["mcpServers"]["jev"]["env"]["TYPESAFE_API_KEY"] == MARKER


def test_codex_shared_with_chatgpt_keeps_the_reference_until_consent(tmp_path: Path) -> None:
    (tmp_path / "Applications" / "ChatGPT.app").mkdir(parents=True)
    (tmp_path / ".codex").mkdir()
    code, text = execute(tmp_path, agents=("codex",))
    assert code == 0, text
    assert "not given the key" in text
    body = (tmp_path / ".codex" / "config.toml").read_text(encoding="utf-8")
    assert "env_vars" in body
    assert "TYPESAFE_API_KEY =" not in body
    code, _ = execute(tmp_path, agents=("codex",), desktop_key=MARKER, secrets=(MARKER,))
    assert code == 0
    body = (tmp_path / ".codex" / "config.toml").read_text(encoding="utf-8")
    assert "env_vars" in body
    assert MARKER in body


def test_symlink_is_preserved(tmp_path: Path) -> None:
    real = tmp_path / "real.json"
    real.write_text("{}\n", encoding="utf-8")
    link = tmp_path / ".claude.json"
    link.symlink_to(real)
    code, _ = execute(tmp_path, agents=("claude-code",))
    assert code == 0
    assert link.is_symlink()
    assert "jev" in real.read_text(encoding="utf-8")


def test_nothing_detected_writes_nothing(tmp_path: Path) -> None:
    before = _fingerprint(tmp_path)
    code, text = execute(tmp_path)
    assert code == 1
    assert "No agents detected" in text
    assert _fingerprint(tmp_path) == before


@pytest.mark.parametrize("descendant", [False, True], ids=["server-only", "server-with-helper"])
def test_verify_handshake(tmp_path: Path, descendant: bool) -> None:
    script = tmp_path / "fake_server.py"
    names = ", ".join(f'"{name}"' for name in EXPECTED_TOOLS)
    helper_pid = tmp_path / "helper.pid"
    pulse = tmp_path / "pulse"
    helper = "import pathlib,sys,time; p=pathlib.Path(sys.argv[1]); exec('while True:\\n p.touch(); time.sleep(0.01)')"
    spawn = (
        (
            "import subprocess,pathlib,time\n"
            f"child = subprocess.Popen([sys.executable, '-c', {helper!r}, {str(pulse)!r}])\n"
            f"pathlib.Path({str(helper_pid)!r}).write_text(str(child.pid))\n"
            f"while not pathlib.Path({str(pulse)!r}).exists(): time.sleep(0.01)\n"
        )
        if descendant
        else ""
    )
    script.write_text(
        "import json,sys\n" + spawn + "init = json.loads(sys.stdin.readline())\n"
        "sys.stdout.write(json.dumps({'jsonrpc':'2.0','id':init['id'],'result':{'serverInfo':{'name':'jev-mcp'}}})+'\\n')\n"
        "sys.stdout.flush()\n"
        "while True:\n"
        "    line = sys.stdin.readline()\n"
        "    if not line:\n"
        "        break\n"
        "    msg = json.loads(line)\n"
        "    if msg.get('method') == 'tools/list':\n"
        f"        tools = [{{'name': name}} for name in [{names}]]\n"
        "        sys.stdout.write(json.dumps({'jsonrpc':'2.0','id':msg['id'],'result':{'tools':tools}})+'\\n')\n"
        "        sys.stdout.flush()\n"
        "        break\n",
        encoding="utf-8",
    )
    try:
        verify_command([sys.executable, str(script)], timeout=5)
        if descendant:
            import time

            # A zombie can keep its PID after group cleanup, but can never update this file.
            time.sleep(0.05)
            last = pulse.stat().st_mtime_ns
            time.sleep(0.1)
            assert pulse.stat().st_mtime_ns == last, "the helper survived the completed handshake"
    finally:
        if helper_pid.exists():
            with suppress(ProcessLookupError):
                os.kill(int(helper_pid.read_text()), signal.SIGKILL)
    script.write_text(
        "import json,sys\n"
        "init = json.loads(sys.stdin.readline())\n"
        "sys.stdout.write(json.dumps({'jsonrpc':'2.0','id':init['id'],'result':{'serverInfo':{'name':'other'}}})+'\\n')\n"
        "sys.stdout.flush()\n",
        encoding="utf-8",
    )
    with pytest.raises(VerifyError, match=r"serverInfo\.name"):
        verify_command([sys.executable, str(script)], timeout=5)


@pytest.mark.parametrize(
    "bad_reply",
    [
        {"id": True},
        {"id": 1.0},
        {"id": 1, "method": "server/request"},
        {"id": 1, "jsonrpc": "1.0"},
        {"id": 1, "error": None},
    ],
    ids=["bool-id", "float-id", "request", "version", "two-outcomes"],
)
def test_verify_ignores_frames_that_are_not_responses(bad_reply: dict[str, object]) -> None:
    forged = {"jsonrpc": "2.0", "result": {"serverInfo": {"name": "forged"}}, **bad_reply}
    good = {"jsonrpc": "2.0", "id": 1, "result": {"serverInfo": {"name": "jev-mcp"}}}
    tools = {"jsonrpc": "2.0", "id": 2, "result": {"tools": [{"name": name} for name in EXPECTED_TOOLS]}}
    child = (
        f"import sys; sys.stdin.readline(); print({json.dumps(forged)!r}); "
        f"print({json.dumps(good)!r}); print({json.dumps(tools)!r}, flush=True); sys.stdin.read()"
    )
    verify_command([sys.executable, "-c", child], timeout=5)


def test_verify_error_carries_the_child_stderr(tmp_path: Path) -> None:
    """A uv-style resolver failure on stderr reaches the operator's summary (ADR-0053)."""
    script = tmp_path / "uv_style_failure.py"
    script.write_text(
        "import sys\n"
        "sys.stderr.write('  x Because the current Python version (3.10.19) does not satisfy\\n')\n"
        "sys.stderr.write('    Python>=3.12, the requirements are unsatisfiable.\\n')\n"
        "sys.stderr.flush()\n"
        "sys.exit(1)\n",
        encoding="utf-8",
    )
    with pytest.raises(VerifyError) as caught:
        verify_command([sys.executable, str(script)], timeout=5)
    message = str(caught.value)
    assert message.partition(";")[0] in {"server closed stdin", "server closed stdout"}
    assert "3.10.19" in message
    assert "Python>=3.12" in message


def test_verify_drains_a_flooding_stderr_before_the_child_answers(tmp_path: Path) -> None:
    """A chatty child cannot fill the stderr pipe and stall the handshake (ADR-0053).

    The flood is several pipe buffers deep and written before the initialize reply; without a
    concurrent drain the child blocks on write and this test dies at the timeout instead.
    """
    script = tmp_path / "flooding_server.py"
    names = ", ".join(f'"{name}"' for name in EXPECTED_TOOLS)
    script.write_text(
        "import json,sys\n"
        "sys.stderr.write(('stderr noise ' * 40 + '\\n') * 2000)\n"
        "sys.stderr.flush()\n"
        "init = json.loads(sys.stdin.readline())\n"
        "sys.stdout.write(json.dumps({'jsonrpc':'2.0','id':init['id'],'result':{'serverInfo':{'name':'jev-mcp'}}})+'\\n')\n"
        "sys.stdout.flush()\n"
        "while True:\n"
        "    line = sys.stdin.readline()\n"
        "    if not line:\n"
        "        break\n"
        "    msg = json.loads(line)\n"
        "    if msg.get('method') == 'tools/list':\n"
        f"        tools = [{{'name': name}} for name in [{names}]]\n"
        "        sys.stdout.write(json.dumps({'jsonrpc':'2.0','id':msg['id'],'result':{'tools':tools}})+'\\n')\n"
        "        sys.stdout.flush()\n"
        "        break\n",
        encoding="utf-8",
    )
    verify_command([sys.executable, str(script)], timeout=10)


def test_verify_reads_a_reply_that_arrived_with_an_earlier_line(tmp_path: Path) -> None:
    """Both replies in one chunk: `readline` buffers the second past the first, where a wait on
    the pipe would never see it. The line reader hands it over instead of timing out."""
    script = tmp_path / "eager_server.py"
    names = ", ".join(f'"{name}"' for name in EXPECTED_TOOLS)
    script.write_text(
        "import json,sys\n"
        "init = json.loads(sys.stdin.readline())\n"
        f"tools = [{{'name': name}} for name in [{names}]]\n"
        "sys.stdout.write(json.dumps({'jsonrpc':'2.0','id':init['id'],'result':{'serverInfo':{'name':'jev-mcp'}}})+'\\n'\n"
        "  + json.dumps({'jsonrpc':'2.0','id':init['id'] + 1,'result':{'tools':tools}})+'\\n')\n"
        "sys.stdout.flush()\n"
        "sys.stdin.read()\n",
        encoding="utf-8",
    )
    verify_command([sys.executable, str(script)], timeout=5)


def test_the_stderr_tail_is_capped(tmp_path: Path) -> None:
    """One enormous stderr line must not flood the installer's summary (ADR-0053)."""
    script = tmp_path / "one_long_line.py"
    script.write_text(
        "import sys\nsys.stderr.write('x' * 100000 + '\\n')\nsys.stderr.flush()\nsys.exit(1)\n", encoding="utf-8"
    )
    with pytest.raises(VerifyError) as caught:
        verify_command([sys.executable, str(script)], timeout=5)
    message = str(caught.value)
    assert "x" * 201 not in message
    assert "[oversized stderr line omitted]" in message


def test_verify_failure_text_is_redacted_like_every_summary_line(tmp_path: Path) -> None:
    """The stderr tail flows through the installer's redaction, never out raw (ADR-0053)."""

    def failing(command: list[str]) -> None:
        raise VerifyError(f"the child said {MARKER}")

    code, text = execute(tmp_path, agents=("claude-code",), verify=failing, secrets=(MARKER,))
    assert code == 1
    assert "verify failed" in text
    assert "the child said" in text
    assert MARKER not in text
    assert "[redacted]" in text


def test_reference_tool_list_matches_the_published_names() -> None:
    """The install handshake expects every published tool: the reference ten plus the extension (ADR-0048)."""
    document = json.loads(Path("docs/reference/ts-0.5.0-tools-list.json").read_text(encoding="utf-8"))
    reference_names = tuple(tool["name"] for tool in document["tools"])
    published = tuple(tool.name for tool in TOOLS)
    assert EXPECTED_TOOLS == published
    assert EXPECTED_TOOLS[: len(reference_names)] == reference_names


def test_remove_drops_both_spec_shapes(tmp_path: Path) -> None:
    """The state hash covers the written entry, not the spec form, so either shape removes."""
    execute(tmp_path, agents=("claude-code",), launch=LAUNCH)
    path = tmp_path / ".claude.json"
    assert SPEC in path.read_text(encoding="utf-8")
    code, text = execute(tmp_path, agents=("claude-code",), remove=True, launch=PYPI_LAUNCH)
    assert code == 0, text
    assert "jev" not in json.loads(path.read_text(encoding="utf-8"))["mcpServers"]

    home = tmp_path / "second"
    execute(home, agents=("claude-code",), launch=PYPI_LAUNCH)
    code, text = execute(home, agents=("claude-code",), remove=True, launch=PYPI_LAUNCH)
    assert code == 0, text
    assert "jev" not in json.loads((home / ".claude.json").read_text(encoding="utf-8"))["mcpServers"]


def test_failed_verify_is_recorded_and_exits_non_zero(tmp_path: Path) -> None:
    """A failed post-write check is reported, recorded as `verified: false`, and non-zero (ADR-0051)."""

    def failing(command: list[str]) -> None:
        raise VerifyError("no answer")

    code, text = execute(tmp_path, agents=("claude-code",), launch=PYPI_LAUNCH, verify=failing)
    assert code == 1
    assert "verify failed: no answer" in text
    path = tmp_path / ".claude.json"
    entry = json.loads(path.read_text(encoding="utf-8"))["mcpServers"]["jev"]
    record = _state_targets(tmp_path)["claude-code"]
    assert record["verified"] is False
    assert record["entry_sha256"] == entry_hash(entry)


@pytest.mark.parametrize("target", ["claude-code", "claude-desktop"])
def test_remove_still_removes_an_entry_that_failed_verify(tmp_path: Path, target: str) -> None:
    def failing(command: list[str]) -> None:
        raise VerifyError("no answer")

    code, _ = execute(tmp_path, agents=(target,), desktop_key=MARKER, launch=PYPI_LAUNCH, verify=failing)
    assert code == 1
    code, text = execute(tmp_path, agents=(target,), remove=True, launch=PYPI_LAUNCH)
    assert code == 0, text
    assert "jev" not in json.loads(layout(tmp_path).target_path(target).read_text(encoding="utf-8"))["mcpServers"]
    assert _state_targets(tmp_path) == {}


def test_retry_verification_of_an_owned_entry_without_rewriting(tmp_path: Path) -> None:
    attempts: list[list[str]] = []

    def verify(command: list[str]) -> None:
        attempts.append(command)
        if len(attempts) < 3:
            raise VerifyError("no answer")

    config = layout(tmp_path).cursor_file()
    code, report = execute(tmp_path, agents=("cursor",), verify=verify)
    assert code == 1, report
    first_stat = config.stat()
    for expected_code in [1, 0, 0]:
        code, report = execute(tmp_path, agents=("cursor",), verify=verify)
        assert code == expected_code, report
        assert (config.stat().st_ino, config.stat().st_mtime_ns) == (first_stat.st_ino, first_stat.st_mtime_ns)
        assert _state_targets(tmp_path)["cursor"]["verified"] is (expected_code == 0)
    assert attempts == [LAUNCH.invoke()] * 3


@pytest.mark.parametrize("fails", [False, True], ids=["pass", "fail"])
def test_all_targets_share_one_verification(tmp_path: Path, fails: bool) -> None:
    calls: list[list[str]] = []
    paths = [layout(tmp_path).claude_code_dir(), layout(tmp_path).cursor_dir()]
    for path in paths:
        path.mkdir()

    def verify(command: list[str]) -> None:
        calls.append(command)
        if fails:
            raise VerifyError("no answer")

    code, text = _run_raw(
        Request(layout=layout(tmp_path), launch=PYPI_LAUNCH, select_all=True, assume_yes=True, verify=verify)
    )
    assert code == int(fails), text
    assert calls == [PYPI_LAUNCH.invoke()]
    records = _state_targets(tmp_path)
    assert set(records) == {"claude-code", "cursor"}
    assert all(record["verified"] is not fails for record in records.values())


@pytest.mark.parametrize("target", ["claude-code", "claude-desktop"])
@pytest.mark.parametrize(
    "old_spec",
    [SPEC, f"{SPEC}[typesafe]", f"{PACKAGE}[typesafe]==0.1.0"],
    ids=["checkout", "checkout-extra", "pinned-extra"],
)
def test_reinstall_migrates_a_checkout_entry_to_the_pinned_spec(tmp_path: Path, target: str, old_spec: str) -> None:
    """Reinstall updates every previously owned launch shape and preserves the desktop key."""
    path = layout(tmp_path).target_path(target)
    if old_spec == SPEC:
        code, report = execute(tmp_path, agents=(target,), desktop_key=MARKER, launch=LAUNCH)
        assert code == 0, report
    else:
        previous = Launch(uvx=UVX, spec=old_spec)
        old = claude_desktop_entry(previous, MARKER) if target == "claude-desktop" else claude_code_entry(previous)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"mcpServers": {"jev": old}}), encoding="utf-8")
        _record_entry(tmp_path, target, path, old)
    assert old_spec in path.read_text(encoding="utf-8")

    code, text = execute(tmp_path, agents=(target,), launch=PYPI_LAUNCH)
    assert code == 0, text
    entry = json.loads(path.read_text(encoding="utf-8"))["mcpServers"]["jev"]
    assert entry["args"] == PYPI_LAUNCH.args()
    assert _state_targets(tmp_path)[target]["entry_sha256"] == entry_hash(entry)
    if target == "claude-desktop":
        assert entry["env"]["TYPESAFE_API_KEY"] == MARKER


def test_reinstall_replaces_an_entry_without_the_python_request(tmp_path: Path) -> None:
    """The ADR-0053 upgrade: a pre-fix entry (no --python) is rewritten by a plain re-run."""
    old: dict[str, object] = {
        "command": UVX,
        "args": ["--from", PIN, PACKAGE],
        "env": {"TYPESAFE_API_KEY": "${TYPESAFE_API_KEY}"},
    }
    path = tmp_path / ".claude.json"
    path.write_text(json.dumps({"mcpServers": {"jev": old}}, indent=2) + "\n", encoding="utf-8")
    _record_entry(tmp_path, "claude-code", path, old)
    code, text = execute(tmp_path, agents=("claude-code",), launch=PYPI_LAUNCH)
    assert code == 0, text
    assert "update" in text
    entry = json.loads(path.read_text(encoding="utf-8"))["mcpServers"]["jev"]
    assert entry["args"] == PYPI_LAUNCH.args()
    assert entry["args"][0] == "--python"
    assert _state_targets(tmp_path)["claude-code"]["entry_sha256"] == entry_hash(entry)


def _record_entry(home: Path, target: str, path: Path, entry: dict[str, object]) -> None:
    """Seed the state file so the installer owns an entry it did not write this run."""
    record: dict[str, object] = {
        "path": str(path),
        "name": "jev",
        "installer_version": "0",
        "entry_sha256": entry_hash(entry),
    }
    write_state(home / ".local" / "state" / "jev-mcp" / "install.json", {"targets": {target: record}})


def _state_targets(home: Path) -> dict[str, dict[str, object]]:
    state = json.loads((home / ".local" / "state" / "jev-mcp" / "install.json").read_text(encoding="utf-8"))
    return state["targets"]


def _pi_adapter(home: Path, package: str) -> None:
    directory = home / ".pi" / "agent"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "settings.json").write_text(
        json.dumps({"packages": [package]}) + "\n",
        encoding="utf-8",
    )


def _plist(home: Path, app: str, bundle_id: str) -> None:
    contents = home / "Applications" / app / "Contents"
    contents.mkdir(parents=True)
    (contents / "Info.plist").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
        '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
        '<plist version="1.0"><dict>'
        f"<key>CFBundleIdentifier</key><string>{bundle_id}</string>"
        "</dict></plist>\n",
        encoding="utf-8",
    )


def _fingerprint(root: Path) -> list[tuple[str, int]]:
    rows: list[tuple[str, int]] = []
    for path in sorted(root.rglob("*")):
        if path.is_file():
            rows.append((str(path.relative_to(root)), path.stat().st_size))
    return rows


def _golden(name: str) -> str:
    return (Path("tests/fixtures/install") / name).read_text(encoding="utf-8")


def test_inserting_a_key_keeps_the_comments_before_the_closing_brace() -> None:
    """Comments between the last sibling and `}` are part of the user's file; the insert keeps them
    and places the new key after them."""
    text = '{\n  "mcp": {\n    "other": {"command": ["echo"]} // keep me\n    // and me\n  }\n}\n'
    updated = jsonedit.assign(text, ["mcp", "jev"], {"command": ["jev"]})
    assert "// keep me" in updated and "// and me" in updated
    assert updated.index("// and me") < updated.index('"jev"')
    parsed = jsonedit.loads(updated)
    assert isinstance(parsed, dict)
    assert parsed["mcp"] == {"other": {"command": ["echo"]}, "jev": {"command": ["jev"]}}


def test_a_failed_config_write_leaves_no_temporary_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "config.json"
    target.write_text("{}\n", encoding="utf-8")

    def failing_fsync(fd: int) -> None:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(os, "fsync", failing_fsync)
    with pytest.raises(OSError):
        atomic_write(target, '{"jev": 1}\n', 0o600, target.read_bytes())
    assert target.read_text(encoding="utf-8") == "{}\n"
    assert [entry.name for entry in tmp_path.iterdir()] == ["config.json"]
    with pytest.raises(OSError):
        write_state(tmp_path / "state.json", {"targets": {}})
    assert sorted(entry.name for entry in tmp_path.iterdir()) == ["config.json"]


def test_toml_quoted_keys_and_literal_strings_are_redacted() -> None:
    assert redact_text("TYPESAFE_API_KEY = 'tsk_single_0001'", ()) == "TYPESAFE_API_KEY = '[redacted]'"
    assert redact_text('"TYPESAFE_API_KEY" = "tsk_quoted_0001"', ()) == '"TYPESAFE_API_KEY" = "[redacted]"'
    reference = "TYPESAFE_API_KEY = '{env:TYPESAFE_API_KEY}'"
    assert redact_text(reference, ()) == reference
    assert redact_text('TYPESAFE_API_KEY = "${TYPESAFE_API_KEY}"', ()) == 'TYPESAFE_API_KEY = "${TYPESAFE_API_KEY}"'


def test_the_dry_run_diff_redacts_other_servers_credentials(tmp_path: Path) -> None:
    """The unified diff's context lines come from the whole file: a neighbouring server's token
    must not ride into stdout (and an agent transcript) beside the inserted entry."""
    token = "ghp_" + "Ab1" * 10
    config = tmp_path / ".cursor" / "mcp.json"
    config.parent.mkdir(parents=True)
    config.write_text(
        json.dumps({"mcpServers": {"github": {"command": "gh-mcp", "env": {"GITHUB_TOKEN": token}}}}, indent=2) + "\n",
        encoding="utf-8",
    )
    code, text = execute(tmp_path, agents=("cursor",), dry_run=True)
    assert code == 0, text
    assert token not in text
    assert config.read_text(encoding="utf-8").count(token) == 1  # the file itself is untouched


def test_agents_picked_at_the_prompt_do_not_fail_on_a_missing_pi_adapter(tmp_path: Path) -> None:
    """A chooser answer names the agents as `--agent` does: a Pi directory without the adapter is
    then not a failure of the run, because Pi was not chosen."""
    (tmp_path / ".pi").mkdir()
    picked: list[str] = []

    def chooser(prompt: str) -> tuple[str, ...]:
        picked.append(prompt)
        return ("codex",)

    code, text = _run_raw(
        Request(
            layout=layout(tmp_path),
            launch=LAUNCH,
            platform="darwin",
            bins={},
            chooser=chooser,
            confirmer=lambda _prompt: True,
        )
    )
    assert picked, "the chooser was never asked"
    assert code == 0, text
    assert "Pi needs the MCP adapter" not in text
    assert (tmp_path / ".codex" / "config.toml").exists()


def test_an_unexpanded_environment_key_is_never_stored_as_the_desktop_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """`${TYPESAFE_API_KEY}` in the environment is no key (ADR-0046 amendment): it is not written into a
    GUI config, and it does not join the redaction set, which would mask every real reference."""
    from jev_judge_mcp.install import cli

    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    key, secrets = cli._desktop_key(True, {"TYPESAFE_API_KEY": "${TYPESAFE_API_KEY}"})  # pyright: ignore[reportPrivateUsage] -- the env read IS the boundary under test
    assert key == ""
    assert secrets == ()


@pytest.mark.parametrize("remove", [False, True])
def test_scout_003_interrupted_state_commit_recovers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, remove: bool
) -> None:
    config = layout(tmp_path).claude_code_file()
    state = layout(tmp_path).state_file()
    if remove:
        assert execute(tmp_path, agents=("claude-code",))[0] == 0
    original_replace = os.replace

    def interrupted(source: Path, destination: Path) -> None:
        if (
            destination == state
            and config.exists()
            and ("jev" in json.loads(config.read_text())["mcpServers"]) != remove
        ):
            raise OSError("simulated interruption")
        original_replace(source, destination)

    with monkeypatch.context() as patch:
        patch.setattr(os, "replace", interrupted)
        with pytest.raises((InstallError, OSError)):
            execute(tmp_path, agents=("claude-code",), remove=remove)
    assert ("jev" in json.loads(config.read_text())["mcpServers"]) != remove
    installed, pending = config.read_bytes(), state.read_bytes()
    config.write_bytes(installed + b" ")
    with pytest.raises(InstallError, match="recovery"):
        execute(tmp_path, agents=("claude-code",), remove=True)
    assert config.read_bytes() == installed + b" "
    assert state.read_bytes() == pending
    config.write_bytes(installed)
    assert execute(tmp_path, agents=("claude-code",), remove=True)[0] == 0
    assert "jev" not in json.loads(config.read_text())["mcpServers"]
    assert _state_targets(tmp_path) == {}


def test_scout_003_overlapping_writers(tmp_path: Path) -> None:
    def overlapping(_: Path) -> None:
        try:
            code, _text = execute(tmp_path, agents=("cursor",))
        except InstallError as error:
            assert "busy" in str(error)
            return
        assert code == 0

    assert execute(tmp_path, agents=("claude-code",), before_write=overlapping)[0] == 0
    expected = {"claude-code"}
    if layout(tmp_path).cursor_file().exists():
        expected.add("cursor")
    assert _state_targets(tmp_path).keys() == expected


@pytest.mark.parametrize("broken", [b"{", b"{}", b'{"targets":{"cursor":{}}}', bytes([255])])
def test_scout_003_invalid_state_blocks_writes(tmp_path: Path, broken: bytes) -> None:
    state = layout(tmp_path).state_file()
    state.parent.mkdir(parents=True, mode=0o700)
    state.write_bytes(broken)
    with pytest.raises(InstallError, match="state"):
        execute(tmp_path, agents=("cursor",))
    assert state.read_bytes() == broken
    assert not layout(tmp_path).cursor_file().exists()


def test_deeply_nested_state_fails_cleanly(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    state = layout(tmp_path).state_file()
    state.parent.mkdir(parents=True, mode=0o700)
    state.write_text('{"targets":{}}')

    def reject_nesting(*_args: object, **_kwargs: object) -> object:
        raise RecursionError("JSON nesting limit")

    monkeypatch.setattr(json, "loads", reject_nesting)
    with pytest.raises(InstallError, match="state"):
        execute(tmp_path, agents=("cursor",))
    assert not layout(tmp_path).cursor_file().exists()


def test_scout_003_named_ownership(tmp_path: Path) -> None:
    req = Request(layout=layout(tmp_path), launch=LAUNCH, agents=("cursor",), assume_yes=True)
    assert run(req).code == 0
    path = req.layout.cursor_file()
    doc = json.loads(path.read_text())
    doc["mcpServers"]["foreign"] = doc["mcpServers"]["jev"]
    path.write_text(json.dumps(doc))
    assert run(replace(req, name="foreign", remove=True)).code == 0
    assert run(replace(req, name="second")).code == 0
    assert run(replace(req, remove=True)).code == 0
    assert run(replace(req, name="second", remove=True)).code == 0
    assert json.loads(path.read_text())["mcpServers"] == {"foreign": doc["mcpServers"]["foreign"]}


def test_scout_007_backups_survive_collisions_and_write_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = [write_backup(tmp_path, "stamp", "cursor", ".json", data) for data in (b"one", b"two")]
    assert [path.read_bytes() for path in paths] == [b"one", b"two"]
    assert all(stat.S_IMODE(path.stat().st_mode) == 0o600 for path in paths)

    def failure(_fd: int, _data: bytes) -> int:
        raise OSError("simulated write failure")

    monkeypatch.setattr(os, "write", failure)
    with pytest.raises(OSError):
        write_backup(tmp_path, "stamp", "cursor", ".json", b"three")
    assert {p for p in tmp_path.rglob("*") if p.is_file()} == set(paths)


@pytest.mark.parametrize("mode", ["explicit", "chooser", "all"])
def test_scout_026_selection_order(tmp_path: Path, mode: str) -> None:
    chooser = mode == "chooser"
    if mode == "all":
        layout(tmp_path).cursor_dir().mkdir(parents=True)
    choices = ("cursor", "claude-code", "cursor")
    calls: list[list[str]] = []
    req = Request(
        layout=layout(tmp_path),
        launch=LAUNCH,
        agents=() if chooser else choices,
        select_all=mode == "all",
        assume_yes=not chooser,
        chooser=lambda _: choices,
        confirmer=lambda _: True,
        verify=calls.append,
    )
    result = run(req)
    assert result.code == 0, result.text
    assert len(calls) == 1
    assert result.text.index("cursor: add") < result.text.index("claude-code: add")
    assert run(req).code == 0
    assert len(calls) == 1


@pytest.mark.parametrize("output", ["x" * 64, "{}\n" * 6])
def test_scout_020_stdout_budget(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, output: str) -> None:
    monkeypatch.setattr(verify_module, "STDOUT_LINE_BYTES", 32, raising=False)
    monkeypatch.setattr(verify_module, "STDOUT_MESSAGES", 4, raising=False)
    script = tmp_path / "local.py"
    script.write_text("import sys\nsys.stdin.readline()\nsys.stdout.write(" + repr(output) + ")\nsys.stdout.flush()\n")
    with pytest.raises(VerifyError, match="limit") as result:
        verify_command([sys.executable, str(script)], timeout=1)
    assert output not in str(result.value)


def test_scout_007_failed_backup_preserves_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = layout(tmp_path).claude_code_file()
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_bytes(b'{"mcpServers":{}}')

    def failure(_fd: int, _data: bytes) -> int:
        raise OSError("simulated backup failure")

    monkeypatch.setattr(os, "write", failure)
    assert execute(tmp_path, agents=("claude-code",))[0] == 1
    assert config.read_bytes() == b'{"mcpServers":{}}'
    assert not layout(tmp_path).state_file().exists()


def test_scout_020_deadline_reaps_child(tmp_path: Path) -> None:
    pidfile = tmp_path / "pid"
    child = "import os,sys,time; open(sys.argv[1], 'w').write(str(os.getpid())); time.sleep(10)"
    with pytest.raises(VerifyError, match="timed out"):
        verify_command([sys.executable, "-c", child, str(pidfile)], timeout=1)
    with pytest.raises(ProcessLookupError):
        os.kill(int(pidfile.read_text()), 0)


def test_scout_020_aggregate_byte_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(verify_module, "STDOUT_TOTAL_BYTES", 16)
    with pytest.raises(VerifyError, match="total byte limit"):
        verify_command([sys.executable, "-c", "print('{}\\n' * 6)"])


@pytest.mark.parametrize(("length", "failure"), [(31, "closed stdout"), (32, "closed stdout"), (33, "line byte limit")])
def test_scout_020_line_boundary(length: int, failure: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(verify_module, "STDOUT_LINE_BYTES", 32)
    with pytest.raises(VerifyError, match=failure):
        verify_command([sys.executable, "-c", f"import sys; sys.stdin.readline(); print('a' * {length})"])


def test_scout_020_non_string_tool_name() -> None:
    replies = [
        '{"jsonrpc":"2.0","id":1,"result":{"serverInfo":{"name":"jev-mcp"}}}',
        '{"jsonrpc":"2.0","id":2,"result":{"tools":[{"name":[]}]}}',
    ]
    child = (
        f"import sys; sys.stdin.readline(); print({replies[0]!r}, flush=True); "
        f"sys.stdin.readline(); sys.stdin.readline(); print({replies[1]!r})"
    )
    with pytest.raises(VerifyError, match="tools/list"):
        verify_command([sys.executable, "-c", child])


@pytest.mark.parametrize("close_stdout", [False, True])
def test_scout_020_early_pipe_close_preserves_child_stderr(monkeypatch: pytest.MonkeyPatch, close_stdout: bool) -> None:
    children: list[subprocess.Popen[bytes]] = []

    def capture(child: subprocess.Popen[bytes]) -> subprocess.Popen[bytes]:
        children.append(child)
        return child

    monkeypatch.setattr(subprocess.Popen, "__enter__", capture)
    # The reply makes the parent send its next request after the child has closed stdin.
    response = json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"serverInfo": {"name": "jev-mcp"}}})
    early_exit = "os.close(1)" if close_stdout else f"print({response!r}, flush=True)"
    child = (
        f"import os, sys, time; sys.stdin.readline(); os.close(0); {early_exit}; "
        "time.sleep(0.08); print('local resolver diagnostic', file=sys.stderr, flush=True)"
    )
    with pytest.raises(VerifyError, match="local resolver diagnostic"):
        verify_command([sys.executable, "-c", child], timeout=2)

    (process,) = children
    assert process.returncode is not None
    assert all(pipe is not None and pipe.closed for pipe in (process.stdin, process.stdout, process.stderr))


def test_overlarge_state_refused_before_config_write(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    state = layout(tmp_path).state_file()
    state.parent.mkdir(parents=True, mode=0o700)
    state.write_text('{"targets":{}}')
    real_stat = os.fstat
    inode = state.stat().st_ino

    def large(fd: int) -> os.stat_result:
        info = real_stat(fd)
        if info.st_ino == inode:
            fields = list(info)
            fields[6] = 1 << 30
            return os.stat_result(fields)
        return info

    monkeypatch.setattr(os, "fstat", large)
    with pytest.raises(InstallError, match="state"):
        execute(tmp_path, agents=("cursor",))
    assert not layout(tmp_path).cursor_file().exists()


@pytest.mark.parametrize("original", [None, b"{}"])
def test_commit_preserves_an_edit_after_the_last_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, original: bytes | None
) -> None:
    from jev_judge_mcp.install import fs

    config = tmp_path / "config.json"
    edit = b'{"other":1}'
    config.write_bytes(edit)
    read = fs.read_bytes

    # All optimistic reads saw the planned snapshot. The commit must use the actual destination.
    def snapshot(path: Path) -> bytes | None:
        return original if path == config else read(path)

    monkeypatch.setattr(fs, "read_bytes", snapshot)
    with pytest.raises((fs.ConfigChangedError, InstallError)) as conflict:
        atomic_write(config, '{"jev":1}', 0o600, original)
    assert "changed" in str(conflict.value)
    saved = [p for p in tmp_path.iterdir() if p.is_file() and p.read_bytes() == edit]
    assert len(saved) == 1
    assert config == saved[0] or str(saved[0]) in str(conflict.value)


def test_recovery_reports_the_displaced_edit_after_interruption(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = layout(tmp_path).claude_code_file()
    config.write_text("{}")
    edit = '{"editor":"saved"}'
    sync = os.fsync

    def save_concurrent_edit(path: Path, state: Mapping[str, object]) -> None:
        write_state(path, state)
        if "pending" in state:
            config.write_text(edit)

    def interrupt_after_swap(fd: int) -> None:
        if "mcpServers" in config.read_text():
            raise KeyboardInterrupt
        sync(fd)

    with monkeypatch.context() as patch:
        patch.setattr("jev_judge_mcp.install.engine.write_state", save_concurrent_edit)
        patch.setattr(os, "fsync", interrupt_after_swap)
        with pytest.raises(KeyboardInterrupt):
            execute(tmp_path, agents=("claude-code",))
    pending = json.loads(layout(tmp_path).state_file().read_text())["pending"]
    saved = config.with_name(pending["staging"])
    assert saved.read_text() == edit
    with pytest.raises(InstallError, match="Reconcile") as conflict:
        execute(tmp_path, agents=("claude-code",))
    assert str(saved) in str(conflict.value)
    assert saved.read_text() == edit
    assert json.loads(layout(tmp_path).state_file().read_text()) == {"targets": {}}


@pytest.mark.parametrize("pending", [False, True], ids=["planning", "recovery"])
def test_installer_rejects_a_nonregular_config_descriptor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pending: bool
) -> None:
    config = layout(tmp_path).cursor_file()
    state = layout(tmp_path).state_file()
    assert execute(tmp_path, agents=("cursor",))[0] == 0
    if pending:
        records = _state_targets(tmp_path)
        (owner,) = records
        write_state(
            state,
            {
                "targets": records,
                "pending": {
                    "key": owner,
                    "path": str(config),
                    "before": "1" * 64,
                    "after": "2" * 64,
                    "record": None,
                },
            },
        )
    before = state.read_bytes()
    inode = config.stat().st_ino
    fstat = os.fstat

    def describe(fd: int) -> os.stat_result:
        st = fstat(fd)
        return os.stat_result((stat.S_IFIFO | 0o600, *st[1:])) if st.st_ino == inode else st

    monkeypatch.setattr(os, "fstat", describe)
    if pending:
        with pytest.raises(InstallError, match=r"recover.*regular file"):
            execute(tmp_path, agents=("cursor",))
    else:
        code, text = execute(tmp_path, agents=("cursor",))
        assert code == 1 and "regular file" in text
    assert state.read_bytes() == before


@pytest.mark.parametrize(
    "source",
    [
        *secret_env(),
        "stored",
        "literal",
        "truncated",
    ],
)
def test_verify_diagnostic_redacts_every_secret_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], source: str
) -> None:
    _fake_home(monkeypatch, tmp_path)
    path = tmp_path / "stored-key"
    monkeypatch.setenv("JEV_MCP_KEY_FILE", str(path))
    marker = secret_env().get(source, "marker-stored-key")
    diagnostic = f"child diagnostic {marker}"
    if source == "literal":
        marker = "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        diagnostic = f'child diagnostic token = "{marker}"'
    elif source == "truncated":
        path.write_text("marker-stored-key-" + "x" * 250)
    elif source == "stored":
        path.write_text(marker)
    elif source == "TYPESAFE_API_KEY":
        settings = load_settings().model_copy(update={"typesafe_api_key": SecretStr(marker)})
        monkeypatch.setattr(install_redact, "load_settings", lambda: settings, raising=False)
    else:
        monkeypatch.setenv(source, marker)
    if source == "truncated":
        diagnostic = "child diagnostic " + path.read_text()
    launcher = tmp_path / "uvx"
    launcher.write_text(f"#!{sys.executable}\nimport sys\nprint({diagnostic!r}, file=sys.stderr, flush=True)\n")
    launcher.chmod(0o700)

    def on_path(name: str) -> str | None:
        return str(launcher) if name == "uvx" else None

    monkeypatch.setattr(install_cli, "which", on_path)
    assert install_cli.main(["--agent", "claude-code", "-y"]) == 1
    output = capsys.readouterr().out
    assert "child diagnostic" in output and "[redacted]" in output
    assert marker not in output


@pytest.mark.parametrize(
    ("swapped", "rewritten", "stage_missing"),
    [
        pytest.param(False, False, False, id="before-exchange-unchanged"),
        pytest.param(False, True, False, id="before-exchange"),
        pytest.param(True, False, False, id="after-exchange"),
        pytest.param(True, True, False, id="after-exchange-rewritten"),
        pytest.param(False, False, True, id="missing-stage-config-before"),
        pytest.param(True, False, True, id="missing-stage-config-after"),
        pytest.param(True, True, True, id="missing-stage-config-rewritten"),
    ],
)
def test_recovery_cleans_staging_without_touching_other_ownership(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, swapped: bool, rewritten: bool, stage_missing: bool
) -> None:
    paths = layout(tmp_path)
    config = paths.claude_code_file()
    assert execute(tmp_path, agents=("claude-code", "cursor"))[0] == 0
    original = config.read_text()
    records = _state_targets(tmp_path)

    def interrupt_exchange(_left: Path, _right: Path) -> None:
        raise KeyboardInterrupt

    sync = install_fs.sync_directory

    def interrupt_sync(path: Path) -> None:
        if path == config.parent and config.read_text() != original:
            raise KeyboardInterrupt
        sync(path)

    with monkeypatch.context() as patch:
        if swapped:
            patch.setattr(install_fs, "sync_directory", interrupt_sync)
        else:
            patch.setattr(install_fs, "_exchange_files", interrupt_exchange)
        with pytest.raises(KeyboardInterrupt):
            execute(tmp_path, agents=("claude-code",), launch=PYPI_LAUNCH)
    journal = paths.state_file().read_bytes()
    pending = json.loads(journal)["pending"]
    staged = config.with_name(pending["staging"])
    assert install_fs.digest(staged.read_bytes()) == pending["before" if swapped else "after"]
    assert install_fs.digest(config.read_bytes()) == pending["after" if swapped else "before"]
    if stage_missing:
        staged.unlink()
    if rewritten:
        document = json.loads(config.read_text())
        document["agent-edit"] = True
        config.write_text(json.dumps(document))

    if rewritten and (swapped or stage_missing):
        rewritten_config = config.read_bytes()
        with pytest.raises(InstallError, match="config changed during installer recovery"):
            execute(tmp_path, agents=("claude-code",), remove=True)
        assert config.read_bytes() == rewritten_config
        assert paths.state_file().read_bytes() == journal
        assert staged.exists() is not stage_missing
        if not stage_missing:
            assert staged.read_text() == original
        return

    before_recovery = config.read_bytes()
    assert execute(tmp_path, agents=("cursor",))[0] == 0
    assert config.read_bytes() == before_recovery
    assert not staged.exists()
    recovered_records = {**records, "claude-code": pending["record"]} if swapped else records
    assert json.loads(paths.state_file().read_bytes()) == {"targets": recovered_records}

    recovered = execute(tmp_path, agents=("claude-code",), remove=True)
    assert recovered[0] == 0, recovered[1]
    assert not staged.exists()
    document = json.loads(config.read_text())
    assert document == ({"mcpServers": {}, "agent-edit": True} if rewritten else {"mcpServers": {}})
    assert _state_targets(tmp_path) == {"cursor": records["cursor"]}


def test_backup_retention_applies_only_after_a_successful_write(tmp_path: Path) -> None:
    paths = layout(tmp_path)
    for target in ("cursor", "claude-code"):
        assert execute(tmp_path, agents=(target,))[0] == 0
        assert execute(tmp_path, agents=(target,), launch=PYPI_LAUNCH)[0] == 0
    for attempt in range(7):
        launch = Launch(uvx=UVX, spec=f"jev-judge-mcp==1.{attempt}")
        result = run(
            Request(
                layout=paths, launch=launch, agents=("claude-code",), stamp=f"20990101T00000{attempt}Z", assume_yes=True
            )
        )
        assert result.code == 0, result.text
    backups = paths.state_dir() / "backups"
    assert [path.parent.name for path in sorted(backups.glob("*/claude-code.json"))] == [
        "20990101T000004Z",
        "20990101T000005Z",
        "20990101T000006Z",
    ]
    (cursor_backup,) = backups.glob("*/cursor.json")
    assert "jev" in json.loads(cursor_backup.read_text())["mcpServers"]
    retained = set(backups.glob("*/claude-code.json"))
    config = paths.claude_code_file()
    previous = config.read_bytes()

    def fail(_path: Path) -> None:
        raise OSError("interrupted before publishing")

    code, text = execute(tmp_path, agents=("claude-code",), launch=LAUNCH, before_write=fail)
    assert code == 1, text
    assert config.read_bytes() == previous
    assert all(path.is_file() for path in retained)
    assert len(list(backups.glob("*/claude-code.json"))) == len(retained) + 1


@pytest.mark.parametrize("bom", [True, False], ids=["bom", "oversize"])
def test_pi_adapter_settings_use_a_bounded_bom_tolerant_read(tmp_path: Path, bom: bool) -> None:
    paths = layout(tmp_path)
    _pi_adapter(tmp_path, "npm:pi-mcp-adapter")
    original = paths.pi_settings().read_bytes()
    data = b"\xef\xbb\xbf" + original if bom else original + b" " * (16 * 1024 * 1024)
    paths.pi_settings().write_bytes(data)
    code, text = execute(tmp_path, agents=("pi",))
    assert code == (0 if bom else 1), text
    assert paths.pi_file().exists() is bom


def test_missing_package_metadata_does_not_write_a_fake_installer_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def missing(_package: str) -> str:
        raise PackageNotFoundError(PACKAGE)

    monkeypatch.setattr(launch_module, "version", missing)
    with pytest.raises(InstallError, match="version cannot be pinned"):
        execute(tmp_path, agents=("cursor",))
    assert not layout(tmp_path).cursor_file().exists()


def test_installer_refuses_an_existing_loose_state_directory(tmp_path: Path) -> None:
    paths = layout(tmp_path)
    paths.state_dir().mkdir(parents=True, mode=0o755)
    with pytest.raises(InstallError, match="private directory"):
        execute(tmp_path, agents=("cursor",))
    assert stat.S_IMODE(paths.state_dir().stat().st_mode) == 0o755
    assert not paths.cursor_file().exists()
