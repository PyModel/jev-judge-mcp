"""Config homes for the installer. Tests pass a throwaway home; nothing here reads a real one."""

import json
import plistlib
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from shutil import which

from jev_judge_mcp.domain import is_json_object
from jev_judge_mcp.install.values import is_json_array

PYTHINKER_DESKTOP_ID = "com.pythinker.desktop"
OMP_SCHEMA = "https://raw.githubusercontent.com/can1357/oh-my-pi/main/packages/coding-agent/src/config/mcp-schema.json"
PI_ADAPTER_HINT = "pi install npm:pi-mcp-adapter"


@dataclass(frozen=True)
class Layout:
    """Every path the installer may read or write, rooted at one home."""

    home: Path
    claude_config_dir: Path | None
    codex_home: Path | None
    xdg_config_home: Path
    pi_agent_dir: Path | None
    omp_profile: str | None
    pythinker_home: Path | None
    xdg_state_home: Path
    applications: Path
    application_support: Path

    def claude_code_file(self) -> Path:
        if self.claude_config_dir is not None:
            return self.claude_config_dir / ".claude.json"
        return self.home / ".claude.json"

    def claude_code_dir(self) -> Path:
        if self.claude_config_dir is not None:
            return self.claude_config_dir
        return self.home / ".claude"

    def claude_desktop_file(self) -> Path:
        return self.application_support / "Claude" / "claude_desktop_config.json"

    def codex_dir(self) -> Path:
        return self.codex_home if self.codex_home is not None else self.home / ".codex"

    def codex_file(self) -> Path:
        return self.codex_dir() / "config.toml"

    def cursor_dir(self) -> Path:
        return self.home / ".cursor"

    def cursor_file(self) -> Path:
        return self.cursor_dir() / "mcp.json"

    def opencode_dir(self) -> Path:
        return self.xdg_config_home / "opencode"

    def opencode_file(self) -> Path:
        directory = self.opencode_dir()
        jsonc = directory / "opencode.jsonc"
        if jsonc.exists():
            return jsonc
        plain = directory / "opencode.json"
        if plain.exists():
            return plain
        return plain

    def pi_dir(self) -> Path:
        return self.pi_agent_dir if self.pi_agent_dir is not None else self.home / ".pi" / "agent"

    def pi_file(self) -> Path:
        return self.pi_dir() / "mcp.json"

    def pi_settings(self) -> Path:
        return self.pi_dir() / "settings.json"

    def omp_file(self) -> Path:
        root = self.home / ".omp"
        if self.omp_profile:
            return root / "profiles" / self.omp_profile / "agent" / "mcp.json"
        return root / "agent" / "mcp.json"

    def pythinker_dir(self) -> Path:
        return self.pythinker_home if self.pythinker_home is not None else self.home / ".pythinker-code"

    def pythinker_file(self) -> Path:
        return self.pythinker_dir() / "mcp.json"

    def state_dir(self) -> Path:
        return self.xdg_state_home / "jev-mcp"

    def state_file(self) -> Path:
        return self.state_dir() / "install.json"

    def target_path(self, target: str) -> Path:
        return {
            "claude-code": self.claude_code_file,
            "claude-desktop": self.claude_desktop_file,
            "codex": self.codex_file,
            "cursor": self.cursor_file,
            "opencode": self.opencode_file,
            "pi": self.pi_file,
            "omp": self.omp_file,
            "pythinker": self.pythinker_file,
        }[target]()


def layout_from_env(
    environ: Mapping[str, str],
    *,
    home: Path,
    applications: Path | None = None,
    application_support: Path | None = None,
) -> Layout:
    """Resolve homes from the process environment. `home` is explicit so tests never use `~`."""
    config_home = environ.get("XDG_CONFIG_HOME")
    state_home = environ.get("XDG_STATE_HOME")
    profile = environ.get("OMP_PROFILE") or environ.get("PI_PROFILE") or None
    return Layout(
        home=home,
        claude_config_dir=_optional_path(environ.get("CLAUDE_CONFIG_DIR")),
        codex_home=_optional_path(environ.get("CODEX_HOME")),
        xdg_config_home=Path(config_home) if config_home else home / ".config",
        pi_agent_dir=_optional_path(environ.get("PI_CODING_AGENT_DIR")),
        omp_profile=profile or None,
        pythinker_home=_optional_path(environ.get("PYTHINKER_CODE_HOME")),
        xdg_state_home=Path(state_home) if state_home else home / ".local" / "state",
        applications=applications if applications is not None else Path("/Applications"),
        application_support=(
            application_support if application_support is not None else home / "Library" / "Application Support"
        ),
    )


def _optional_path(value: str | None) -> Path | None:
    if value is None or value == "":
        return None
    return Path(value)


def pi_adapter_installed(settings_path: Path) -> bool:
    """True when Pi's settings list the MCP adapter package."""
    if not settings_path.is_file():
        return False
    try:
        parsed: object = json.loads(settings_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if not is_json_object(parsed):
        return False
    packages = parsed.get("packages")
    if not is_json_array(packages):
        return False
    for item in packages:
        if isinstance(item, str) and "pi-mcp-adapter" in item:
            return True
    return False


def pythinker_desktop_installed(applications: Path) -> bool:
    """True only when a bundle's CFBundleIdentifier is the Pythinker desktop id.

    A case-insensitive path match is not enough: `/Applications/PyThinker.app` on some
    machines is a different product (`org.pymodel.pythinker`).
    """
    if not applications.is_dir():
        return False
    for app in applications.iterdir():
        if "pythinker" not in app.name.lower() or not app.name.lower().endswith(".app"):
            continue
        plist_path = app / "Contents" / "Info.plist"
        if not plist_path.is_file():
            continue
        try:
            with plist_path.open("rb") as handle:
                info: object = plistlib.load(handle)
        except (OSError, plistlib.InvalidFileException, ValueError):
            continue
        if is_json_object(info) and info.get("CFBundleIdentifier") == PYTHINKER_DESKTOP_ID:
            return True
    return False


def chatgpt_installed(applications: Path) -> bool:
    return (applications / "ChatGPT.app").exists()


def command_exists(name: str) -> bool:
    return which(name) is not None


def default_bins(names: tuple[str, ...]) -> dict[str, bool]:
    return {name: command_exists(name) for name in names}


def running_on_macos(platform: str | None = None) -> bool:
    return (platform if platform is not None else sys.platform) == "darwin"
