"""The stdio transport: newline-delimited JSON-RPC on stdin and stdout, as the reference's SDK speaks it.

It replaces the SDK's `stdio_server` for what that one cannot do:

- `JSON.parse` accepts a lone surrogate escape (`"\\ud83d"`) and keeps it. The SDK's parser (jiter)
  rejected the whole line, so the request got no reply; it also accepts `NaN` and `Infinity`, which
  `JSON.parse` rejects. Lines are read with `decode_json` instead, which keeps a lone surrogate as one
  code point, with those constants refused.
- `JSON.stringify` writes a lone surrogate as a `\\udxxx` escape. pydantic cannot encode one, and the
  SDK's writer would fail on the first reply that echoes it; such a frame goes through `stringify_compact`.
- The stdin read runs in a thread that shutdown can abandon: a blocked `readline()` would otherwise
  hold the server open after a signal.

Undecodable input bytes still become U+FFFD, as Node's `Buffer.toString('utf8')` makes them in the
reference. While serving, fd 1 points at stderr so stray writes by handlers or children miss the wire.
The streams are plain anyio memory streams, not the SDK's context-carrying ones: a stdin reader has
no per-message contextvars to hand over, and the dispatcher then runs handlers in its own context.
"""

import contextlib
import os
import sys
from collections.abc import AsyncGenerator, Generator
from io import TextIOWrapper
from typing import BinaryIO

import anyio
import anyio.to_thread
from anyio.streams.memory import MemoryObjectReceiveStream, MemoryObjectSendStream
from mcp.shared.message import SessionMessage
from mcp.types import JSONRPCMessage, jsonrpc_message_adapter
from pydantic_core import PydanticSerializationError

from jev_judge_mcp.domain import decode_json
from jev_judge_mcp.serialize import stringify_compact

type Streams = tuple[MemoryObjectReceiveStream[SessionMessage | Exception], MemoryObjectSendStream[SessionMessage]]


def parse_line(line: str) -> SessionMessage | Exception:
    """One JSON-RPC message read as `JSON.parse` reads it, or the error for the dispatcher to drop."""
    try:
        value = decode_json(line)
        return SessionMessage(jsonrpc_message_adapter.validate_python(value, by_name=False))
    except (ValueError, RecursionError) as error:  # a pydantic ValidationError is a ValueError
        return error


def encode_frame(message: JSONRPCMessage) -> str:
    """The message as one JSON line's text, with any lone surrogate escaped as `JSON.stringify` does."""
    try:
        return message.model_dump_json(by_alias=True, exclude_unset=True)
    except PydanticSerializationError:
        return stringify_compact(message.model_dump(mode="json", by_alias=True, exclude_unset=True))


class _AbandonableLines(anyio.AsyncFile[str]):
    async def readline(self) -> str:
        return await anyio.to_thread.run_sync(self.wrapped.readline, abandon_on_cancel=True)


@contextlib.asynccontextmanager
async def stdio_streams() -> AsyncGenerator[Streams]:
    """Read and write streams for `Server.run` over the process's stdin and stdout."""
    stdin = _AbandonableLines(TextIOWrapper(sys.stdin.buffer, encoding="utf-8", errors="replace"))
    with _claim_stdout() as wire:
        stdout = anyio.wrap_file(TextIOWrapper(wire, encoding="utf-8"))
        read_writer, read_stream = anyio.create_memory_object_stream[SessionMessage | Exception](0)
        write_stream, write_reader = anyio.create_memory_object_stream[SessionMessage](0)

        async def read() -> None:
            async with read_writer:
                async for line in stdin:
                    await read_writer.send(parse_line(line))

        async def write() -> None:
            async with write_reader:
                async for session_message in write_reader:
                    await stdout.write(encode_frame(session_message.message) + "\n")
                    await stdout.flush()

        async with anyio.create_task_group() as tg:
            tg.start_soon(read)
            tg.start_soon(write)
            yield read_stream, write_stream


@contextlib.contextmanager
def _claim_stdout() -> Generator[BinaryIO]:
    """The wire on a private descriptor while fd 1 points at stderr; fd 1 is restored on exit."""
    sys.stdout.flush()
    private = os.dup(1)
    os.dup2(2, 1)
    try:
        with os.fdopen(private, "wb", closefd=False) as wire:
            yield wire
    finally:
        os.dup2(private, 1)
        os.close(private)
