"""Read and update one named server entry in a JSON, JSONC, or TOML config."""

import json
import tomllib
from collections.abc import Mapping

import tomlkit
from tomlkit.exceptions import TOMLKitError
from tomlkit.items import Table

from jev_judge_mcp.domain import is_json_object
from jev_judge_mcp.install.errors import ConfigParseError, ConfigShapeError
from jev_judge_mcp.install.jsonedit import assign, delete, loads
from jev_judge_mcp.install.layout import OMP_SCHEMA
from jev_judge_mcp.install.values import is_json_array


def read_text(path_text: str) -> object:
    return loads(path_text)


def json_entry_path(target: str, document: object, name: str) -> list[str]:
    """Where `name` lives. Unknown parent shapes raise before any write."""
    if target == "opencode":
        return _opencode_path(document, name)
    if not is_json_object(document):
        raise ConfigShapeError("top level is not an object")
    if "mcpServers" not in document:
        return ["mcpServers", name]
    if not is_json_object(document["mcpServers"]):
        raise ConfigShapeError("mcpServers is not an object")
    return ["mcpServers", name]


def read_entry(document: object, path: list[str]) -> dict[str, object] | None:
    current: object = document
    for key in path[:-1]:
        if not is_json_object(current):
            raise ConfigShapeError(f"{key} is not an object")
        if key not in current:
            return None
        current = current[key]
    if not is_json_object(current):
        where = ".".join(path[:-1]) or "top level"
        raise ConfigShapeError(f"{where} is not an object")
    if path[-1] not in current:
        return None
    entry = current[path[-1]]
    if not is_json_object(entry):
        raise ConfigShapeError(f"{path[-1]} is not an object")
    return dict(entry)


def server_map(document: object, path: list[str]) -> dict[str, object]:
    """The object that holds server names, used only to notice unrelated Jev entries."""
    current: object = document
    for key in path[:-1]:
        if not is_json_object(current) or key not in current:
            return {}
        current = current[key]
    if not is_json_object(current):
        return {}
    return dict(current)


def render_json(target: str, text: str, name: str, entry: Mapping[str, object]) -> str:
    if target == "omp" and text.strip() == "":
        text = json.dumps({"$schema": OMP_SCHEMA, "mcpServers": {}}, indent=2) + "\n"
    document = loads(text) if text.strip() else _empty_object()
    path = json_entry_path(target, document, name)
    return assign(text if text.strip() else "{}\n", path, dict(entry))


def remove_json(target: str, text: str, name: str) -> str:
    document = loads(text) if text.strip() else _empty_object()
    path = json_entry_path(target, document, name)
    return delete(text, path)


def _empty_object() -> dict[str, object]:
    return {}


def render_codex(text: str, name: str, entry: Mapping[str, object]) -> str:
    document = _codex_document(text)
    servers = _servers_table(document, create=True)
    servers[name] = _entry_table(entry)
    rendered = tomlkit.dumps(document)
    return rendered if rendered.endswith("\n") else rendered + "\n"


def remove_codex(text: str, name: str) -> str:
    document = _codex_document(text)
    servers = _toml_table(document, "mcp_servers", create=False)
    if servers is not None and name in servers:
        del servers[name]
    rendered = tomlkit.dumps(document)
    return rendered if rendered.endswith("\n") else rendered + "\n"


def read_codex_entry(text: str, name: str) -> tuple[dict[str, object] | None, dict[str, object]]:
    """The named entry, plus the plain server map for the unrelated-server notice.

    Reading uses `tomllib` so a string where a table belongs fails closed. Writing still uses
    `tomlkit`, which keeps comments in the rest of the file.
    """
    if text.strip() == "":
        return None, {}
    try:
        loaded = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigParseError("invalid TOML") from exc
    servers = loaded.get("mcp_servers")
    if servers is None:
        return None, {}
    if not is_json_object(servers):
        raise ConfigShapeError("mcp_servers is not a table")
    others = {key: _plain(item) for key, item in servers.items()}
    entry = servers.get(name)
    if entry is None:
        return None, others
    if not is_json_object(entry):
        raise ConfigShapeError(f"{name} is not a table")
    return dict(entry), others


def _opencode_path(document: object, name: str) -> list[str]:
    if not is_json_object(document):
        raise ConfigShapeError("top level is not an object")
    if "mcp" not in document:
        return ["mcp", name]
    mcp = document["mcp"]
    if not is_json_object(mcp):
        raise ConfigShapeError("mcp is not an object")
    if "servers" not in mcp:
        return ["mcp", name]
    if not is_json_object(mcp["servers"]):
        raise ConfigShapeError("mcp.servers is not an object")
    return ["mcp", "servers", name]


def _codex_document(text: str) -> tomlkit.TOMLDocument:
    try:
        return tomlkit.parse(text)
    except TOMLKitError as exc:
        raise ConfigParseError("invalid TOML") from exc


def _servers_table(document: tomlkit.TOMLDocument, *, create: bool) -> Table:
    servers = _toml_table(document, "mcp_servers", create=create)
    if servers is None:
        raise ConfigShapeError("mcp_servers is missing")
    return servers


def _toml_table(document: tomlkit.TOMLDocument, key: str, *, create: bool) -> Table | None:
    if key not in document:
        if not create:
            return None
        created = tomlkit.table()
        document.add(key, created)
        return created
    found: object = document[key]
    if not isinstance(found, Table):
        raise ConfigShapeError(f"{key} is not a table")
    return found


def _entry_table(entry: Mapping[str, object]) -> Table:
    table = tomlkit.table()
    for key, value in entry.items():
        _add_toml(table, key, value)
    return table


def _add_toml(table: Table, key: str, value: object) -> None:
    if isinstance(value, str):
        table.add(key, value)
        return
    if is_json_array(value):
        strings: list[str] = []
        for item in value:
            if not isinstance(item, str):
                raise ConfigShapeError(f"{key} is not a list of strings")
            strings.append(item)
        table.add(key, strings)
        return
    if is_json_object(value):
        nested = tomlkit.table()
        for name, item in value.items():
            _add_toml(nested, name, item)
        table.add(key, nested)
        return
    raise ConfigShapeError(f"{key} is not a TOML value this installer writes")


def _plain(value: object) -> object:
    if is_json_object(value):
        return {key: _plain(item) for key, item in value.items()}
    if is_json_array(value):
        return [_plain(item) for item in value]
    return value
