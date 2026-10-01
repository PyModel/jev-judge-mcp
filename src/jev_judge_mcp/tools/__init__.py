"""The Jev MCP tools, published in the reference's registration order (`parity-manifest.json` `tools_list_order`).

`jev_score` is an extension published after the ten snapshot tools (ADR-0048, divergence
`score-tool-extension`), and `jev_file_judge` after it (ADR-0077, divergence `file-judge-tools`):
the snapshot order is preserved as a prefix and each extension is pinned by its own contract tests.
"""

from jev_judge_mcp.tools import (
    classify,
    compare,
    decide,
    extract,
    file_judge,
    find,
    gate,
    rerank,
    review,
    score,
    screen,
    verify,
)
from jev_judge_mcp.tools.base import JevTool, Runtime, ToolError, ToolResult
from jev_judge_mcp.tools.toolset import Toolset

TOOLS: tuple[JevTool, ...] = (
    verify.TOOL,
    screen.TOOL,
    find.TOOL,
    classify.TOOL,
    decide.TOOL,
    rerank.TOOL,
    compare.TOOL,
    extract.TOOL,
    review.TOOL,
    gate.TOOL,
    score.TOOL,
    file_judge.TOOL,
)

__all__ = ["TOOLS", "JevTool", "Runtime", "ToolError", "ToolResult", "Toolset"]
