"""A streamable-HTTP body decoded the way mcp 2.2.0 does (`pydantic_core.from_json`).

That decoder keeps `NaN` and the infinities. stdio's `decode_json` refuses those constants, so the
existing stdio tests never reach the argument parser. The parser rejects the number anyway.
"""

import math
from typing import Any, cast

import pydantic_core
import pytest

from jev_judge_mcp.tools.arguments import ArgumentsError, compile_argument_schema

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"n": {"type": "number"}},
    "required": ["n"],
    "additionalProperties": False,
}

PARSE = compile_argument_schema("jev_x", SCHEMA)


def test_nonfinite_number_from_an_http_body_is_an_arguments_error() -> None:
    for literal in ("NaN", "Infinity", "-Infinity"):
        arguments_json = '{"name":"jev_x","arguments":{"n":' + literal + "}}"
        body = ('{"jsonrpc":"2.0","id":1,"method":"tools/call","params":' + arguments_json + "}").encode()
        raw = cast(dict[str, Any], pydantic_core.from_json(body))
        arguments = cast(dict[str, Any], cast(dict[str, Any], raw["params"])["arguments"])
        number = arguments["n"]
        assert isinstance(number, float)
        assert not math.isfinite(number)
        with pytest.raises(ArgumentsError, match="Number must be finite at n"):
            PARSE(arguments)
