"""What each agent config entry launches. The package name is always `jev-judge-mcp` (ADR-0049)."""

import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from jev_judge_mcp.domain import is_json_object
from jev_judge_mcp.install.errors import InstallError
from jev_judge_mcp.install.redact import is_reference

PACKAGE = "jev-judge-mcp"
SERVER_NAME = "jev-mcp"
REFERENCE = "${TYPESAFE_API_KEY}"
OPENCODE_REFERENCE = "{env:TYPESAFE_API_KEY}"


@dataclass(frozen=True)
class Launch:
    """Absolute `uvx` and the local `--from` spec written into every entry."""

    uvx: str
    spec: str

    def args(self) -> list[str]:
        return ["--from", self.spec, PACKAGE]

    def invoke(self) -> list[str]:
        return [self.uvx, *self.args()]


def local_launch(uvx: str, package_root: Path | None = None) -> Launch:
    """Pin every client at this checkout until the package is published (ADR-0033)."""
    root = package_root if package_root is not None else find_package_root()
    if not root.is_absolute():
        root = root.resolve()
    return Launch(uvx=uvx, spec=f"{root}[typesafe]")


def find_package_root() -> Path:
    """The checkout that contains this installer's `pyproject.toml`."""
    for parent in Path(__file__).resolve().parents:
        manifest = parent / "pyproject.toml"
        if not manifest.is_file():
            continue
        try:
            loaded = tomllib.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise InstallError(f"could not read {manifest}") from exc
        project = loaded.get("project")
        name = project.get("name") if is_json_object(project) else None
        if name == PACKAGE:
            return parent
    raise InstallError(
        "could not find the local jev-judge-mcp checkout; refusing to write a PyPI package spec before publication"
    )


def claude_code_entry(launch: Launch) -> dict[str, object]:
    return {"type": "stdio", "command": launch.uvx, "args": launch.args(), "env": {"TYPESAFE_API_KEY": REFERENCE}}


def claude_desktop_entry(launch: Launch, key: str) -> dict[str, object]:
    return {"command": launch.uvx, "args": launch.args(), "env": {"TYPESAFE_API_KEY": key}}


def cursor_entry(launch: Launch) -> dict[str, object]:
    return {"command": launch.uvx, "args": launch.args(), "env": {"TYPESAFE_API_KEY": REFERENCE}}


def opencode_entry(launch: Launch) -> dict[str, object]:
    return {
        "type": "local",
        "command": launch.invoke(),
        "environment": {"TYPESAFE_API_KEY": OPENCODE_REFERENCE},
    }


def pi_entry(launch: Launch) -> dict[str, object]:
    """Eager and direct: the adapter lists the jev tools in the model's initial tool list.

    The adapter's default server is lazy and proxy-only: no jev tool reaches the model until it
    walks the gateway (`mcp({server})` → `mcp({connect})` → `mcp({describe})` → `mcp({tool})`), and
    recorded agent runs show they never start that walk on their own (ADR-0036). `lifecycle:
    "eager"` connects at startup, `directTools: true` registers every tool individually, and
    `toolPrefix: "none"` keeps the published names (`jev_verify`, ...), so inside pi the tools are
    visible and callable like any builtin.
    """
    return {
        "command": launch.uvx,
        "args": launch.args(),
        "env": {"TYPESAFE_API_KEY": REFERENCE},
        "lifecycle": "eager",
        "directTools": True,
        "toolPrefix": "none",
    }


def omp_entry(launch: Launch) -> dict[str, object]:
    return {"type": "stdio", "command": launch.uvx, "args": launch.args(), "env": {"TYPESAFE_API_KEY": REFERENCE}}


def pythinker_entry(launch: Launch, key: str | None) -> dict[str, object]:
    entry: dict[str, object] = {"command": launch.uvx, "args": launch.args()}
    if key is not None:
        entry["env"] = {"TYPESAFE_API_KEY": key}
    return entry


def codex_entry(launch: Launch, key: str | None) -> dict[str, object]:
    entry: dict[str, object] = {
        "command": launch.uvx,
        "args": launch.args(),
        "env_vars": ["TYPESAFE_API_KEY"],
    }
    if key is not None:
        entry["env"] = {"TYPESAFE_API_KEY": key}
    return entry


def with_preserved_literal(desired: dict[str, object], current: Mapping[str, object] | None) -> dict[str, object]:
    """Keep a literal this installer already stored when this run did not ask to replace it.

    A later run without `--desktop-key` must not strip the key from a shared GUI file, and must
    not print it. The value is copied only inside the entry that will be compared or written.
    """
    if current is None:
        return desired
    env = current.get("env")
    if not is_json_object(env):
        return desired
    value = env.get("TYPESAFE_API_KEY")
    if not isinstance(value, str) or is_reference(value):
        return desired
    merged = dict(desired)
    merged_env = dict(env_mapping(merged.get("env")))
    merged_env["TYPESAFE_API_KEY"] = value
    merged["env"] = merged_env
    return merged


def env_mapping(value: object) -> dict[str, object]:
    if is_json_object(value):
        return dict(value)
    return {}
