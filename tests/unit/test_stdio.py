"""The stdio transport's line parser and frame encoder read and write JSON as `JSON.parse`/`JSON.stringify` do."""

import json

import pytest
from mcp.types import JSONRPCRequest, JSONRPCResponse
from pydantic.aliases import AliasChoices

from jev_judge_mcp.stdio import encode_frame, parse_line


def test_lone_surrogate_escape_is_kept() -> None:
    parsed = parse_line('{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"x\\ud83dy"}}\n')
    assert not isinstance(parsed, Exception)
    assert isinstance(parsed.message, JSONRPCRequest)
    assert parsed.message.params == {"name": "x\ud83dy"}


@pytest.mark.parametrize(
    "line",
    [
        "not json\n",
        '{"jsonrpc":"2.0","id":1,"method":"ping","params":{"n":NaN}}\n',
        '{"jsonrpc":"2.0","id":1,"method":"ping","params":{"n":"\\ud83d"},}\n',
        '{"jsonrpc":"1.0","id":1,"method":"x\\ud83d"}\n',
    ],
    ids=["text", "nan", "trailing-comma", "not-json-rpc"],
)
def test_what_json_parse_rejects_stays_an_error(line: str) -> None:
    assert isinstance(parse_line(line), Exception)


def test_frames_without_surrogates_are_pydantic_s() -> None:
    message = JSONRPCResponse(jsonrpc="2.0", id=1, result={"text": "é😀"})
    assert encode_frame(message) == message.model_dump_json(by_alias=True, exclude_unset=True)


def test_lone_surrogates_are_escaped_and_split_pairs_rejoined() -> None:
    message = JSONRPCResponse(jsonrpc="2.0", id="a\udc00", result={"text": "x\ud83dy é \ud83d\ude00"})
    frame = encode_frame(message)
    assert frame == '{"jsonrpc":"2.0","id":"a\\udc00","result":{"text":"x\\ud83dy é 😀"}}'
    frame.encode()  # well-formed UTF-8
    assert json.loads(frame)["result"]["text"] == "x\ud83dy é 😀"


def test_every_setting_the_server_reads_is_scrubbed_from_its_spawn_env() -> None:
    """The developer's shell cannot change a spawned server's behavior.

    Every environment variable `Settings` reads (its validation aliases, derived here from the
    schema) is scrubbed before a test spawns the server. The alias set, not the scrub list, is
    the source of truth: a knob added to `Settings` without joining `_SCRUBBED_ENV` makes every
    spawned server behavior depend on the developer's shell — `JEV_MCP_MAX_INFLIGHT` exported in
    a verification shell changed the security suite's servers this way.
    """
    from jev_judge_mcp.settings import Settings
    from tests.support.stdio import _SCRUBBED_ENV  # pyright: ignore[reportPrivateUsage]

    aliases: set[str] = set()
    for field in Settings.model_fields.values():
        alias = field.validation_alias
        if isinstance(alias, str):
            aliases.add(alias)
        elif isinstance(alias, AliasChoices):
            aliases.update(choice for choice in alias.choices if isinstance(choice, str))

    assert aliases, "the schema derivation found no aliases; the guard would pass vacuously"
    missing = aliases - set(_SCRUBBED_ENV)
    assert not missing, f"settings read from the developer's shell by spawned servers: {sorted(missing)}"
