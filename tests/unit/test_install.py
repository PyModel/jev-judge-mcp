"""Installer fixtures use a throwaway home. They never read or write the real user config."""

import json
import os
import stat
import sys
from collections.abc import Callable
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

import pytest

from jev_judge_mcp.install import cli as install_cli
from jev_judge_mcp.install import launch as launch_module
from jev_judge_mcp.install.engine import Request, run
from jev_judge_mcp.install.errors import InstallError
from jev_judge_mcp.install.fs import entry_hash
from jev_judge_mcp.install.launch import (
    PACKAGE,
    Launch,
    checkout_launch,
    find_package_root,
    pi_entry,
    pypi_launch,
    supported_spec,
)
from jev_judge_mcp.install.layout import Layout
from jev_judge_mcp.install.verify import EXPECTED_TOOLS, VerifyError, verify_command
from jev_judge_mcp.server import installer_requested
from jev_judge_mcp.tools import TOOLS

UVX = "/opt/uv/uvx"
SPEC = "/work/jev-mcp[typesafe]"
PIN = f"{PACKAGE}[typesafe]=={version(PACKAGE)}"
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
    assert launch.spec.endswith("[typesafe]")
    assert Path(launch.spec.removesuffix("[typesafe]")).is_absolute()
    assert not any(part == "jev-mcp" or part.startswith("jev-mcp==") or part.startswith("jev-mcp[") for part in args)


def test_default_launch_pins_the_installed_version() -> None:
    """From a checkout or a wheel, the default spec is the running package pinned on PyPI (ADR-0051)."""
    launch = pypi_launch(UVX)
    assert launch.spec == PIN
    assert launch.args() == ["--from", PIN, PACKAGE]


def test_default_launch_without_metadata_names_the_alternative(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing(name: str) -> str:
        raise PackageNotFoundError(name)

    monkeypatch.setattr(launch_module, "version", missing)
    with pytest.raises(InstallError, match="--from-checkout"):
        pypi_launch(UVX)


def test_find_package_root_without_a_checkout_names_the_flag(tmp_path: Path) -> None:
    with pytest.raises(InstallError, match="source checkout"):
        find_package_root(start=tmp_path)


def test_specs_accept_exactly_the_two_supported_shapes() -> None:
    assert supported_spec(PIN)
    assert supported_spec("jev-judge-mcp[typesafe]==0.1.1rc1")
    assert supported_spec(SPEC)
    assert supported_spec("/home/me/jev-judge-mcp[typesafe]")
    for wrong in (
        PACKAGE,
        f"{PACKAGE}[typesafe]",
        "jev-mcp[typesafe]",
        f"{PACKAGE}==0.1.1",
        f"{PACKAGE}[typesafe]==",
        f"{PACKAGE}[typesafe]==0.1.1 more",
        "work/jev-mcp[typesafe]",
        "",
    ):
        assert not supported_spec(wrong), wrong


def test_engine_refuses_an_unsupported_spec(tmp_path: Path) -> None:
    with pytest.raises(InstallError, match="unsupported launch spec"):
        execute(tmp_path, agents=("claude-code",), launch=Launch(uvx=UVX, spec="jev-mcp[typesafe]"))


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

    def uvx(name: str) -> str | None:
        return UVX

    monkeypatch.setattr(install_cli, "which", uvx)


def test_wheel_layout_dry_run_renders_the_pinned_pypi_spec(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The critique's wheel probe, inverted: no checkout anywhere, and `install` still works."""

    def no_checkout(start: Path | None = None) -> Path:
        return find_package_root(start=tmp_path / "nowhere")

    _fake_home(monkeypatch, tmp_path)
    monkeypatch.setattr(launch_module, "find_package_root", no_checkout)
    code = install_cli.main(["--dry-run", "-a", "claude-code"])
    out = capsys.readouterr().out
    assert code == 0, out
    assert PIN in out
    assert SPEC not in out
    assert list(tmp_path.rglob("*.json")) == []  # dry-run wrote nothing


def test_checkout_flag_renders_the_checkout_spec(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _fake_home(monkeypatch, tmp_path)
    checkout = str(find_package_root())
    code = install_cli.main(["--dry-run", "-a", "claude-code", "--from-checkout"])
    assert code == 0
    out = capsys.readouterr().out
    assert f"{checkout}[typesafe]" in out
    assert f"=={version(PACKAGE)}" not in out


def test_default_from_a_checkout_is_still_the_pypi_spec(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _fake_home(monkeypatch, tmp_path)
    code = install_cli.main(["--dry-run", "-a", "claude-code"])
    assert code == 0
    out = capsys.readouterr().out
    assert PIN in out
    assert str(find_package_root()) not in out


def test_checkout_flag_without_a_checkout_fails_accurately(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _fake_home(monkeypatch, tmp_path)
    monkeypatch.setattr(launch_module, "find_package_root", lambda: find_package_root(start=tmp_path))
    code = install_cli.main(["--dry-run", "-a", "claude-code", "--from-checkout"])
    assert code == 1
    out = capsys.readouterr().out
    assert "source checkout" in out
    assert "before publication" not in out


def test_each_target_gains_one_entry(tmp_path: Path) -> None:
    _pi_adapter(tmp_path)
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
    assert pi["mcpServers"]["jev"]["directTools"] is True
    assert pi["mcpServers"]["jev"]["toolPrefix"] == "none"
    omp = json.loads((tmp_path / ".omp" / "agent" / "mcp.json").read_text(encoding="utf-8"))
    assert omp["$schema"].endswith("mcp-schema.json")
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
    assert entry["directTools"] is True
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


def test_remove_drops_only_our_entry(tmp_path: Path) -> None:
    execute(tmp_path, agents=("claude-code",))
    path = tmp_path / ".claude.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    document["mcpServers"]["evaluate"] = {"command": "/usr/bin/evaluate", "args": []}
    path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    code, text = execute(tmp_path, agents=("claude-code",), remove=True)
    assert code == 0, text
    left = json.loads(path.read_text(encoding="utf-8"))
    assert "jev" not in left["mcpServers"]
    assert left["mcpServers"]["evaluate"]["command"] == "/usr/bin/evaluate"


def test_remove_leaves_a_foreign_entry(tmp_path: Path) -> None:
    path = tmp_path / ".claude.json"
    original = json.dumps({"mcpServers": {"jev": {"command": "/bin/other", "args": []}}}, indent=2) + "\n"
    path.write_text(original, encoding="utf-8")
    code, text = execute(tmp_path, agents=("claude-code",), remove=True)
    assert code == 0, text
    assert "foreign" in text
    assert path.read_text(encoding="utf-8") == original


def test_unknown_shapes_are_left_untouched(tmp_path: Path) -> None:
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
    _pi_adapter(tmp_path)
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


def test_json_targets_match_goldens(tmp_path: Path) -> None:
    _pi_adapter(tmp_path)
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


def test_cursor_detected_from_the_binary(tmp_path: Path) -> None:
    code, text = _run_raw(
        Request(
            layout=layout(tmp_path),
            launch=LAUNCH,
            agents=("cursor",),
            assume_yes=True,
            platform="darwin",
            bins={"cursor": True},
        )
    )
    assert code == 0, text
    assert (tmp_path / ".cursor" / "mcp.json").exists()


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


def test_opencode_v2_servers_map(tmp_path: Path) -> None:
    path = tmp_path / ".config" / "opencode" / "opencode.json"
    path.parent.mkdir(parents=True)
    path.write_text('{"mcp": {"servers": {"other": {"type": "local", "command": ["echo"]}}}}\n', encoding="utf-8")
    code, text = execute(tmp_path, agents=("opencode",))
    assert code == 0, text
    document = json.loads(path.read_text(encoding="utf-8"))
    assert "jev" not in document["mcp"]
    assert document["mcp"]["servers"]["jev"]["type"] == "local"
    assert document["mcp"]["servers"]["other"]["command"] == ["echo"]


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


def test_verify_handshake(tmp_path: Path) -> None:
    script = tmp_path / "fake_server.py"
    names = ", ".join(f'"{name}"' for name in EXPECTED_TOOLS)
    script.write_text(
        "import json,sys\n"
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
    verify_command([sys.executable, str(script)], timeout=5)
    script.write_text(
        "import json,sys\n"
        "init = json.loads(sys.stdin.readline())\n"
        "sys.stdout.write(json.dumps({'jsonrpc':'2.0','id':init['id'],'result':{'serverInfo':{'name':'other'}}})+'\\n')\n"
        "sys.stdout.flush()\n",
        encoding="utf-8",
    )
    with pytest.raises(VerifyError, match=r"serverInfo\.name"):
        verify_command([sys.executable, str(script)], timeout=5)


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


def test_remove_still_removes_an_entry_that_failed_verify(tmp_path: Path) -> None:
    def failing(command: list[str]) -> None:
        raise VerifyError("no answer")

    code, _ = execute(tmp_path, agents=("claude-code",), launch=PYPI_LAUNCH, verify=failing)
    assert code == 1
    code, text = execute(tmp_path, agents=("claude-code",), remove=True, launch=PYPI_LAUNCH)
    assert code == 0, text
    assert "jev" not in json.loads((tmp_path / ".claude.json").read_text(encoding="utf-8"))["mcpServers"]
    assert _state_targets(tmp_path) == {}


def test_passed_verify_is_recorded(tmp_path: Path) -> None:
    calls: list[list[str]] = []
    code, text = execute(tmp_path, agents=("claude-code",), launch=PYPI_LAUNCH, verify=calls.append)
    assert code == 0, text
    assert calls == [PYPI_LAUNCH.invoke()]
    assert _state_targets(tmp_path)["claude-code"]["verified"] is True


def test_reinstall_migrates_a_checkout_entry_to_the_pinned_spec(tmp_path: Path) -> None:
    """The ADR-0051 migration: one plain re-run rewrites entries this installer owns."""
    code, _ = execute(tmp_path, agents=("claude-code",), launch=LAUNCH)
    assert code == 0
    path = tmp_path / ".claude.json"
    assert SPEC in path.read_text(encoding="utf-8")
    code, text = execute(tmp_path, agents=("claude-code",), launch=PYPI_LAUNCH)
    assert code == 0, text
    entry = json.loads(path.read_text(encoding="utf-8"))["mcpServers"]["jev"]
    assert entry["args"] == ["--from", PIN, PACKAGE]
    assert _state_targets(tmp_path)["claude-code"]["entry_sha256"] == entry_hash(entry)


def _state_targets(home: Path) -> dict[str, dict[str, object]]:
    state = json.loads((home / ".local" / "state" / "jev-mcp" / "install.json").read_text(encoding="utf-8"))
    return state["targets"]


def _pi_adapter(home: Path) -> None:
    directory = home / ".pi" / "agent"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "settings.json").write_text(
        json.dumps({"packages": ["git:github.com/nicobailon/pi-mcp-adapter"]}) + "\n",
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
