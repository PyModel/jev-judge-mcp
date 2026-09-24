"""What each agent config entry launches. The package name is always `jev-judge-mcp` (ADR-0049).

The default launch is the version-pinned PyPI package; a checkout is an explicit
`--from-checkout` choice (ADR-0051). Every entry also requests a Python that satisfies the
package's `Requires-Python` (ADR-0053), so `uvx` cannot resolve the package against an
interpreter it refuses.
"""

import json
import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, distribution, version
from pathlib import Path

from jev_judge_mcp.domain import is_json_object
from jev_judge_mcp.install.errors import InstallError
from jev_judge_mcp.install.redact import is_reference

PACKAGE = "jev-judge-mcp"
SERVER_NAME = "jev-mcp"
REFERENCE = "${TYPESAFE_API_KEY}"
OPENCODE_REFERENCE = "{env:TYPESAFE_API_KEY}"
EXTRA = "typesafe"


@dataclass(frozen=True)
class Launch:
    """Absolute `uvx`, the `--from` spec, and the `--python` request written into every entry (ADR-0051, ADR-0053)."""

    uvx: str
    spec: str
    python: str | None = None

    def args(self) -> list[str]:
        # An argument array, never a shell string: the request rides as one argv element.
        head = ["--python", self.python] if self.python is not None else []
        return [*head, "--from", self.spec, PACKAGE]

    def invoke(self) -> list[str]:
        return [self.uvx, *self.args()]


def pypi_launch(uvx: str) -> Launch:
    """The running package pinned on PyPI: `jev-judge-mcp[typesafe]==<installed version>`.

    Needs no source checkout, so a wheel install can run the installer, and no written entry
    depends on a checkout that may later move or disappear (ADR-0051). In real use the first
    launch of a not-yet-cached version may download the package from PyPI.
    """
    return Launch(uvx=uvx, spec=f"{PACKAGE}[{EXTRA}]=={installed_version()}", python=requires_python())


def checkout_launch(uvx: str, package_root: Path | None = None) -> Launch:
    """This checkout: `<absolute checkout>[typesafe]`, found the way ADR-0033 found it."""
    root = package_root if package_root is not None else find_package_root()
    if not root.is_absolute():
        root = root.resolve()
    return Launch(uvx=uvx, spec=f"{root}[{EXTRA}]", python=requires_python())


def installed_version() -> str:
    """The installed distribution's version; the pin ADR-0051 writes by default."""
    try:
        return version(PACKAGE)
    except PackageNotFoundError as exc:
        raise InstallError(
            f"{PACKAGE} is not installed in this environment, so its version cannot be pinned; "
            "install the package, or use --from-checkout"
        ) from exc


def requires_python() -> str | None:
    """The installed distribution's `Requires-Python`, as `uvx --python`'s value (ADR-0053).

    Taken from the metadata, not hardcoded: the request must track what the package itself
    declares. None when the metadata does not say, in which case the entry behaves exactly as
    before this request existed.
    """
    try:
        raw = distribution(PACKAGE).metadata.get("Requires-Python")
    except PackageNotFoundError:
        return None
    value = " ".join((raw or "").split())  # metadata headers can line-wrap a specifier
    return value or None


def local_install_warning() -> str | None:
    """The checkout warning: the default pin names a PyPI build without this tree's changes (ADR-0053).

    True for an editable install and any other local source install, which `importlib.metadata`
    records in `direct_url.json` as a `file://` URL. A PyPI wheel install carries no such record,
    so it prints nothing.
    """
    try:
        raw = distribution(PACKAGE).read_text("direct_url.json")
    except PackageNotFoundError:
        return None
    if not raw:
        return None
    try:
        parsed: object = json.loads(raw)
    except json.JSONDecodeError:
        return None
    url = parsed.get("url") if is_json_object(parsed) else None
    if not isinstance(url, str) or not url.startswith("file://"):
        return None
    return (
        "note: this installer runs from a local checkout, so the pinned PyPI build "
        f"{PACKAGE}[{EXTRA}]=={installed_version()} does not include your local changes; "
        "pass --from-checkout to install this tree instead"
    )


def find_package_root(start: Path | None = None) -> Path:
    """The checkout that contains this installer's `pyproject.toml`."""
    origin = start if start is not None else Path(__file__)
    for parent in origin.resolve().parents:
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
        "could not find a jev-judge-mcp source checkout (a directory whose pyproject.toml names "
        "jev-judge-mcp); --from-checkout needs one"
    )


def supported_spec(spec: str) -> bool:
    """Exactly the two shapes this installer writes (ADR-0051): the version-pinned PyPI spec and
    the checkout spec. Anything else — the bare package name, another project, a relative path —
    is refused before any config is written."""
    pinned = re.fullmatch(rf"{re.escape(PACKAGE)}\[{re.escape(EXTRA)}\]==(\S+)", spec)
    if pinned:
        return '"' not in pinned.group(1)
    checkout = re.fullmatch(r"(.+)\[typesafe\]", spec)
    if checkout:
        return Path(checkout.group(1)).is_absolute()
    return False


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
