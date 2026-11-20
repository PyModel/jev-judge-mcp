"""Read and update one named server entry in a JSON, JSONC, or TOML config."""

import json
import tomllib
from collections.abc import Iterator, Mapping

import tomlkit
from tomlkit.container import Container
from tomlkit.exceptions import TOMLKitError
from tomlkit.items import AoT, Comment, Item, Key, Null, Table, Whitespace

from jev_judge_mcp.domain import is_json_array, is_json_object
from jev_judge_mcp.install.errors import ConfigParseError, ConfigShapeError
from jev_judge_mcp.install.jsonedit import assign, delete, loads


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
        text = json.dumps({"mcpServers": {}}, indent=2) + "\n"
    document = loads(text)
    path = json_entry_path(target, document, name)
    return assign(text if text.strip() else "{}\n", path, dict(entry))


def remove_json(target: str, text: str, name: str) -> str:
    document = loads(text)
    path = json_entry_path(target, document, name)
    return delete(text, path)


def render_codex(text: str, name: str, entry: Mapping[str, object]) -> str:
    document = _codex_document(text)
    servers = _servers_table(document, create=True)
    assert servers is not None
    previous = servers.item(name) if name in servers else servers
    replacement = _entry_table(entry)
    if isinstance(previous, Table):
        tail, start = _trailing_trivia(previous)
        _last_body(replacement).extend(tail[start:])
        del tail[start:]
    servers[name] = replacement
    return _final_newline(tomlkit.dumps(document))


def remove_codex(text: str, name: str) -> str:
    document = _codex_document(text)
    servers = _servers_table(document, create=False)
    if servers is None or name not in servers:
        return text
    existing = servers.item(name)
    body = servers.value.body
    index = next(i for i, (key, _) in enumerate(body) if key is not None and key.key == name)
    del servers[name]
    if not servers and servers.is_super_table():
        servers = tomlkit.table()
        document["mcp_servers"] = servers
        body, index = servers.value.body, 0
        body.append((None, Null()))
    if isinstance(existing, Table):
        tail, start = _trailing_trivia(existing)
        # Deletion leaves the slot vacant. Render the next table's heading at that same offset.
        body[index] = (None, tomlkit.ws("".join(item.as_string() for _, item in tail[start:])))
    return _final_newline(tomlkit.dumps(document))


def _last_body(table: Table) -> list[tuple[Key | None, Item]]:
    body = table.value.body
    for _, item in reversed(body):
        if isinstance(item, (Comment, Null, Whitespace)):
            continue
        if isinstance(item, Table):
            return _last_body(item)
        if isinstance(item, AoT):
            return _last_body(item.body[-1])
        break
    return body


def _trailing_trivia(table: Table) -> tuple[list[tuple[Key | None, Item]], int]:
    body = _last_body(table)
    start = len(body)
    while start and body[start - 1][0] is None:
        start -= 1
    if not any(isinstance(item, Comment) for _, item in body[start:]):
        start = len(body)
    return body, start


def _final_newline(text: str) -> str:
    return text if text.endswith("\n") else text + "\n"


def read_codex(text: str) -> dict[str, object]:
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigParseError("invalid TOML") from exc


def codex_entry_path(document: dict[str, object], name: str) -> list[str]:
    if "mcp_servers" in document and not is_json_object(document["mcp_servers"]):
        raise ConfigShapeError("mcp_servers is not a table")
    return ["mcp_servers", name]


def codex_headings_preserved(before: str, after: str, name: str) -> bool:
    actual = iter(_codex_headings(after, name))
    return all(
        any(path == new_path and comments == new_comments[-len(comments) :] for new_path, new_comments in actual)
        for path, comments in _codex_headings(before, name)
    )


def _codex_headings(text: str, name: str) -> list[tuple[tuple[str, ...], tuple[str, ...]]]:
    """The comment blocks that precede foreign table headers, in source order."""
    pending: list[str] = []
    headings: list[tuple[tuple[str, ...], tuple[str, ...]]] = []
    for path, item in _toml_items(_codex_document(text), ()):
        if isinstance(item, Comment):
            pending.append(item.as_string())
        elif not isinstance(item, (Null, Whitespace)):
            if pending and isinstance(item, (Table, AoT)) and path[:2] != ("mcp_servers", name):
                headings.append((path, tuple(pending)))
            pending.clear()
    return headings


def _toml_items(container: Container, path: tuple[str, ...]) -> Iterator[tuple[tuple[str, ...], Item]]:
    for key, item in container.body:
        child = (*path, key.key) if key is not None else path
        if isinstance(item, Table):
            if not item.is_super_table():
                yield child, item
            yield from _toml_items(item.value, child)
        elif isinstance(item, AoT):
            for table in item.body:
                yield child, table
                yield from _toml_items(table.value, child)
        else:
            yield child, item


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
    servers = mcp["servers"]
    if not is_json_object(servers):
        raise ConfigShapeError("mcp.servers is not an object")
    if (
        isinstance(servers.get("type"), str)
        or isinstance(servers.get("url"), str)
        or is_json_array(servers.get("command"))
        or isinstance(servers.get("enabled"), bool)
    ):
        return ["mcp", name]
    return ["mcp", "servers", name]


def _codex_document(text: str) -> tomlkit.TOMLDocument:
    try:
        return tomlkit.parse(text)
    except TOMLKitError as exc:
        raise ConfigParseError("invalid TOML") from exc


def _servers_table(document: tomlkit.TOMLDocument, *, create: bool) -> Table | None:
    key = "mcp_servers"
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
