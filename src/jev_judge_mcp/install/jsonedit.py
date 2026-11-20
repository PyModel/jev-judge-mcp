"""Edit one JSON object key, including JSONC comments, without rewriting the rest of the file.

Comment masking keeps indexes aligned with the original text: a comment becomes spaces, the
structure is located on that copy, and the splice is applied to the original. A shape this
module does not recognise raises, and the caller writes nothing.
"""

import json
import re

from jev_judge_mcp.install.errors import ConfigParseError, ConfigShapeError

_INDENT = re.compile(r"\n([ \t]+)\S")


def mask_comments(text: str) -> str:
    """Return `text` with comments replaced by spaces. The length and newlines stay the same."""
    out = list(text)
    index = 0
    limit = len(text)
    while index < limit:
        char = text[index]
        if char == '"':
            index = _end_of_string(text, index)
            continue
        if char == "/" and index + 1 < limit and text[index + 1] == "/":
            end = index
            while end < limit and text[end] not in "\r\n":
                out[end] = " "
                end += 1
            index = end
            continue
        if char == "/" and index + 1 < limit and text[index + 1] == "*":
            end = index + 2
            out[index] = " "
            out[index + 1] = " "
            while end + 1 < limit and not (text[end] == "*" and text[end + 1] == "/"):
                if text[end] not in "\r\n":
                    out[end] = " "
                end += 1
            if end + 1 >= limit:
                raise ConfigParseError("unterminated block comment")
            out[end] = " "
            out[end + 1] = " "
            index = end + 2
            continue
        index += 1
    return "".join(out)


def _mask_trailing_commas(masked: str) -> str:
    out = list(masked)
    index = 0
    previous = ""
    while index < len(masked):
        char = masked[index]
        if char == '"':
            index = _end_of_string(masked, index)
            previous = '"'
            continue
        if char == "," and previous not in ("", "{", "[", ","):
            following = _skip(masked, index + 1)
            if following < len(masked) and masked[following] in "}]":
                out[index] = " "
        if char not in " \t\r\n":
            previous = char
        index += 1
    return "".join(out)


def loads(text: str) -> object:
    """Parse JSON or JSONC. An empty file is an empty object."""
    if not text.strip():
        return dict[str, object]()
    try:
        parsed: object = json.loads(_mask_trailing_commas(mask_comments(text)))
    except json.JSONDecodeError as exc:
        raise ConfigParseError(exc.msg) from exc
    except RecursionError as exc:
        raise ConfigParseError("JSON is too deeply nested") from exc
    return parsed


def detect_indent(text: str) -> str | None:
    """The file's indent unit, or None when the file is a single line."""
    if "\n" not in text:
        return None
    match = _INDENT.search(text)
    if match is None:
        return "  "
    return match.group(1)


def assign(text: str, path: list[str], value: object) -> str:
    """Set `path` to `value` and return the new document. `path` walks objects only."""
    if not path:
        raise ConfigShapeError("empty config path")
    original = text if text.strip() else "{}\n"
    masked = mask_comments(original)
    loads(original)
    indent = detect_indent(original)
    newline = "\r\n" if "\r\n" in original else "\n"
    located = _locate(masked, path)
    if isinstance(located, _Found):
        pad = _line_indent(original, located.value_start)
        rendered = _place(_render(value, indent), pad, newline)
        return _finish(original, original[: located.value_start] + rendered + original[located.value_end :])
    pad = _child_indent(original, located.parent_start, indent)
    nested = _nest(located.remaining[1:], value) if located.remaining[1:] else value
    key = json.dumps(located.remaining[0], ensure_ascii=False)
    body = _place(_render(nested, indent), pad, newline)
    return _finish(
        original,
        _insert(original, masked, located.parent_start, located.parent_end, f"{key}: {body}", pad, indent, newline),
    )


def delete(text: str, path: list[str]) -> str:
    """Remove the value at `path`. A missing key leaves the text unchanged."""
    if text.strip() == "":
        return text
    masked = mask_comments(text)
    loads(text)
    located = _locate(masked, path)
    if isinstance(located, _Missing):
        return text
    return _finish(text, _remove_property(text, masked, located))


def check_path(text: str, path: list[str]) -> None:
    """Reject duplicate keys along the edited path, even when the last value already matches."""
    _locate(mask_comments(text if text.strip() else "{}"), path)


def _nest(keys: list[str], value: object) -> object:
    built: object = value
    for key in reversed(keys):
        built = {key: built}
    return built


class _Found:
    def __init__(self, value_start: int, value_end: int, key_start: int) -> None:
        self.value_start = value_start
        self.value_end = value_end
        self.key_start = key_start


class _Missing:
    def __init__(self, parent_start: int, parent_end: int, remaining: list[str]) -> None:
        self.parent_start = parent_start
        self.parent_end = parent_end
        self.remaining = remaining


def _locate(masked: str, path: list[str]) -> _Found | _Missing:
    index = _skip(masked, 0)
    if index >= len(masked) or masked[index] != "{":
        raise ConfigShapeError("top level is not an object")
    try:
        return _locate_in(masked, index, len(masked.rstrip(" \t\r\n")), path)
    except RecursionError as exc:
        raise ConfigParseError("JSON is too deeply nested") from exc


def _locate_in(masked: str, start: int, end: int, path: list[str]) -> _Found | _Missing:
    items = _object_items(masked, start, end)
    key = path[0]
    matches = [item for item in items if item[0] == key]
    if len(matches) > 1:
        raise ConfigShapeError(f"duplicate key {key!r} on the server entry path; make it unique")
    if not matches:
        return _Missing(start, end, path)
    ((_, key_start, _, value_start, value_end),) = matches
    if len(path) == 1:
        return _Found(value_start, value_end, key_start)
    if masked[_skip(masked, value_start)] != "{":
        raise ConfigShapeError(f"{key} is not an object")
    return _locate_in(masked, value_start, value_end, path[1:])


def _object_items(masked: str, start: int, end: int) -> list[tuple[str, int, int, int, int]]:
    index = start + 1
    items: list[tuple[str, int, int, int, int]] = []
    while True:
        index = _skip(masked, index)
        if index >= end or masked[index] == "}":
            return items
        if masked[index] != '"':
            raise ConfigParseError("expected a key")
        key, key_start, key_end = _string_span(masked, index)
        index = _skip(masked, key_end)
        if index >= end or masked[index] != ":":
            raise ConfigParseError("expected ':'")
        value_start, value_end = _value_span(masked, index + 1)
        items.append((key, key_start, key_end, value_start, value_end))
        index = _skip(masked, value_end)
        if index < end and masked[index] == ",":
            index += 1
            continue
        if index < end and masked[index] == "}":
            return items
        raise ConfigParseError("expected ',' or '}'")


def _value_span(masked: str, index: int) -> tuple[int, int]:
    index = _skip(masked, index)
    if index >= len(masked):
        raise ConfigParseError("truncated value")
    char = masked[index]
    if char == '"':
        _, start, end = _string_span(masked, index)
        return start, end
    if char in "{[":
        return _container_span(masked, index)
    if char in "-0123456789":
        end = index + 1
        while end < len(masked) and masked[end] in "0123456789.eE+-":
            end += 1
        return index, end
    for literal in ("true", "false", "null"):
        if masked.startswith(literal, index):
            return index, index + len(literal)
    raise ConfigParseError("unknown value")


def _container_span(masked: str, index: int) -> tuple[int, int]:
    opener = masked[index]
    closer = "}" if opener == "{" else "]"
    cursor = index + 1
    while True:
        cursor = _skip(masked, cursor)
        if cursor >= len(masked):
            raise ConfigParseError("truncated container")
        if masked[cursor] == closer:
            return index, cursor + 1
        if opener == "{":
            if masked[cursor] != '"':
                raise ConfigParseError("expected a key")
            _, _, cursor = _string_span(masked, cursor)
            cursor = _skip(masked, cursor)
            if cursor >= len(masked) or masked[cursor] != ":":
                raise ConfigParseError("expected ':'")
            cursor += 1
        _, cursor = _value_span(masked, cursor)
        cursor = _skip(masked, cursor)
        if cursor < len(masked) and masked[cursor] == ",":
            cursor += 1
            continue
        if cursor < len(masked) and masked[cursor] == closer:
            return index, cursor + 1
        raise ConfigParseError("expected ',' or a closer")


def _string_span(masked: str, index: int) -> tuple[str, int, int]:
    end = _end_of_string(masked, index)
    try:
        loaded = json.loads(masked[index:end])
    except json.JSONDecodeError as exc:
        raise ConfigParseError(exc.msg) from exc
    if not isinstance(loaded, str):
        raise ConfigParseError("expected a string")
    return loaded, index, end


def _end_of_string(text: str, index: int) -> int:
    if index >= len(text) or text[index] != '"':
        raise ConfigParseError("expected a string")
    cursor = index + 1
    while cursor < len(text):
        if text[cursor] == "\\":
            cursor += 2
            continue
        if text[cursor] == '"':
            return cursor + 1
        cursor += 1
    raise ConfigParseError("unterminated string")


def _skip(text: str, index: int) -> int:
    while index < len(text) and text[index] in " \t\r\n":
        index += 1
    return index


def _render(value: object, indent: str | None) -> str:
    if indent is None:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return json.dumps(value, ensure_ascii=False, indent=indent)


def _place(rendered: str, pad: str, newline: str) -> str:
    lines = rendered.split("\n")
    if len(lines) == 1:
        return rendered
    return newline.join(line if number == 0 else pad + line for number, line in enumerate(lines))


def _line_indent(text: str, index: int) -> str:
    line_start = text.rfind("\n", 0, index) + 1
    cursor = line_start
    while cursor < len(text) and text[cursor] in " \t":
        cursor += 1
    return text[line_start:cursor]


def _child_indent(text: str, parent_start: int, indent: str | None) -> str:
    parent_pad = _line_indent(text, parent_start)
    return parent_pad + (indent or "")


def _insert(
    text: str,
    masked: str,
    parent_start: int,
    parent_end: int,
    property_text: str,
    child_pad: str,
    indent: str | None,
    newline: str,
) -> str:
    close = parent_end - 1
    inner = text[parent_start + 1 : close]
    # Comments are spaces in the masked copy, so an object that only holds comments counts as empty.
    if masked[parent_start + 1 : close].strip() == "":
        parent_pad = _line_indent(text, parent_start)
        if indent is None:
            body = "{" + inner + property_text + "}"
        elif inner.strip() == "":
            body = "{" + newline + child_pad + property_text + newline + parent_pad + "}"
        else:
            body = "{" + inner.rstrip() + newline + child_pad + property_text + newline + parent_pad + "}"
        return text[:parent_start] + body + text[parent_end:]
    cursor = close - 1
    while cursor > parent_start and masked[cursor] in " \t\r\n":
        cursor -= 1
    comma = "" if masked[cursor] == "," else ","
    parent_pad = _line_indent(text, parent_start)
    # The gap between the last value and the closing brace holds whitespace and comments only
    # (comments are spaces in the masked copy, so the cursor skipped them): keep the comments,
    # drop the whitespace, and put the new key after them so it sits beside its siblings.
    kept = text[cursor + 1 : close].rstrip()
    if indent is None:
        return text[: cursor + 1] + comma + text[cursor + 1 : close] + property_text + text[close:]
    tail = comma + kept + newline + child_pad + property_text + newline + parent_pad
    return text[: cursor + 1] + tail + text[close:]


def _remove_property(original: str, masked: str, found: _Found) -> str:
    start, end = found.key_start, found.value_end
    following = _skip(masked, end)
    if following < len(masked) and masked[following] == ",":
        return original[:start] + original[end:following] + original[following + 1 :]
    preceding = start - 1
    while preceding >= 0 and masked[preceding] in " \t\r\n":
        preceding -= 1
    if preceding >= 0 and masked[preceding] == ",":
        return original[:preceding] + original[preceding + 1 : start] + original[end:]
    return original[:start] + original[end:]


def _finish(original: str, updated: str) -> str:
    if original.endswith("\n") and not updated.endswith("\n"):
        return updated + ("\r\n" if original.endswith("\r\n") else "\n")
    return updated


_COMMENT_OR_STRING = re.compile(r'"(?:[^"\\]|\\.)*"|//[^\r\n]*|/\*[\s\S]*?\*/')


def preserved_comments(text: str, path: list[str]) -> list[str]:
    """Comments outside the edited property, in source order."""
    if not text.strip():
        return []
    located = _locate(mask_comments(text), path)
    if isinstance(located, _Found):
        text = text[: located.key_start] + " " + text[located.value_end :]
    return [match[0] for match in _COMMENT_OR_STRING.finditer(text) if match[0].startswith("/")]
