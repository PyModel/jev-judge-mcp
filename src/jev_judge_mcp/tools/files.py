"""What the multi-file diff paths share (ADR-0066): the file-list shape and the combined frame.

`jev_review` and `jev_gate` both split a `[{path, patch}]` diff and frame one reply from every
per-file provider call, so the shape check and the usage-summing frame live here once.
"""

from typing import cast

from jev_judge_mcp.domain import Usage
from jev_judge_mcp.providers import Evaluation
from jev_judge_mcp.tools.base import ToolError


def file_patches(diff: object) -> list[dict[str, str]]:
    """A file-list `diff` as sent: one `{path, patch}` record per item, or a `ToolError`."""
    if not isinstance(diff, list):
        raise ToolError("diff file list was not a list")
    files: list[dict[str, str]] = []
    for raw in cast(list[object], diff):
        if not isinstance(raw, dict):
            raise ToolError("diff file list item was not an object")
        record = cast(dict[str, object], raw)
        files.append({"path": str(record["path"]), "patch": str(record["patch"])})
    return files


def combined(evaluations: list[Evaluation]) -> Evaluation:
    """One frame for per-file asks: usage summed across every call, request id kept when one sent it."""
    first = evaluations[0]
    request_id = next((item.request_id for item in evaluations if item.request_id), None)
    return Evaluation(
        {},
        Usage(
            sum(item.usage.input_tokens for item in evaluations),
            sum(item.usage.output_tokens for item in evaluations),
        ),
        first.provider,
        first.model,
        request_id=request_id,
    )
