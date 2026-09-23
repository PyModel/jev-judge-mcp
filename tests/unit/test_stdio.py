"""The stdio transport's line parser and frame encoder read and write JSON as `JSON.parse`/`JSON.stringify` do."""

import json

import pytest
from mcp.types import JSONRPCRequest, JSONRPCResponse

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
