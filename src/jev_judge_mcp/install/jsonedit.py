"""Edit one JSON object key, including JSONC comments, without rewriting the rest of the file.

Comment masking keeps indexes aligned with the original text: a comment becomes spaces, the
structure is located on that copy, and the splice is applied to the original. A shape this
module does not recognise raises, and the caller writes nothing.
"""

import json
import re

from jev_judge_mcp.domain import is_json_object
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
            while end < limit and text[end] != "\n":
                out[end] = " "
                end += 1
            index = end
            continue
        if char == "/" and index + 1 < limit and text[index + 1] == "*":
            end = index + 2
            out[index] = " "
            out[index + 1] = " "
            while end + 1 < limit and not (text[end] == "*" and text[end + 1] == "/"):
                if text[end] != "\n":
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


def loads(text: str) -> object:
    """Parse JSON or JSONC. An empty file is an empty object."""
    if text.strip() == "":
        empty: dict[str, object] = {}
        return empty
    try:
        parsed: object = json.loads(mask_comments(text))
    except json.JSONDecodeError as exc:
        raise ConfigParseError(exc.msg) from exc
    return parsed


def has_comments(text: str) -> bool:
    return mask_comments(text) != text


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
    _require_object(loads(original), path)
    indent = detect_indent(original)
    located = _locate(masked, path)
    if isinstance(located, _Found):
        pad = _line_indent(original, located.value_start)
        rendered = _place(_render(value, indent), pad)
        return _finish(original, original[: located.value_start] + rendered + original[located.value_end :])
    pad = _child_indent(original, located.parent_start, indent)
    nested = _nest(located.remaining[1:], value) if located.remaining[1:] else value
    key = json.dumps(located.remaining[0], ensure_ascii=False)
    body = _place(_render(nested, indent), pad)
    return _finish(
        original,
        _insert(original, masked, located.parent_start, located.parent_end, f"{key}: {body}", pad, indent),
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


def _require_object(value: object, path: list[str]) -> None:
    current: object = value
    walked: list[str] = []
    for key in path[:-1]:
        if current == {} and not walked:
            return
        if not is_json_object(current):
            raise ConfigShapeError(f"{'.'.join(walked) or 'top level'} is not an object")
        if key not in current:
            return
        current = current[key]
        walked.append(key)
    if not is_json_object(current):
        where = ".".join(path[:-1]) or "top level"
        raise ConfigShapeError(f"{where} is not an object")


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
    start, end = _value_span(masked, index)
    return _locate_in(masked, start, end, path)


def _locate_in(masked: str, start: int, end: int, path: list[str]) -> _Found | _Missing:
    items = _object_items(masked, start, end)
    key = path[0]
    found = next((item for item in items if item[0] == key), None)
    if found is None:
        return _Missing(start, end, path)
    _, key_start, _, value_start, value_end = found
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


def _place(rendered: str, pad: str) -> str:
    lines = rendered.split("\n")
    if len(lines) == 1:
        return rendered
    return "\n".join(line if number == 0 else pad + line for number, line in enumerate(lines))


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
) -> str:
    close = parent_end - 1
    inner = text[parent_start + 1 : close]
    # Comments are spaces in the masked copy, so an object that only holds comments counts as empty.
    if masked[parent_start + 1 : close].strip() == "":
        parent_pad = _line_indent(text, parent_start)
        if indent is None:
            body = "{" + property_text + "}"
        elif inner.strip() == "":
            body = "{\n" + child_pad + property_text + "\n" + parent_pad + "}"
        else:
            body = "{" + inner.rstrip() + "\n" + child_pad + property_text + "\n" + parent_pad + "}"
        return text[:parent_start] + body + text[parent_end:]
    cursor = close - 1
    while cursor > parent_start and masked[cursor] in " \t\r\n":
        cursor -= 1
    comma = "" if masked[cursor] == "," else ","
    parent_pad = _line_indent(text, parent_start)
    # Replace the gap before the closing brace so the new key sits beside its siblings.
    tail = comma + "\n" + child_pad + property_text + "\n" + parent_pad
    return text[: cursor + 1] + tail + text[close:]


def _remove_property(original: str, masked: str, found: _Found) -> str:
    end = found.value_end
    cursor = _skip_inline(masked, end)
    if cursor < len(masked) and masked[cursor] == ",":
        end = cursor + 1
        start = _property_line_start(original, found.key_start)
        return original[:start] + original[end:]
    start = found.key_start
    cursor = start - 1
    while cursor >= 0 and masked[cursor] in " \t\r\n":
        cursor -= 1
    if cursor >= 0 and masked[cursor] == ",":
        start = cursor
    else:
        start = _property_line_start(original, found.key_start)
    return original[:start] + original[end:]


def _property_line_start(text: str, key_start: int) -> int:
    previous = text.rfind("\n", 0, key_start)
    if previous < 0:
        return key_start
    return previous + 1


def _skip_inline(text: str, index: int) -> int:
    while index < len(text) and text[index] in " \t":
        index += 1
    return index


def _finish(original: str, updated: str) -> str:
    if original.endswith("\n") and not updated.endswith("\n"):
        return updated + "\n"
    return updated
